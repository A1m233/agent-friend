import { create } from "zustand";

import {
  connectVoiceInput,
  prewarmVoiceInput,
  type VoiceInputConnection,
} from "@/services/api/voiceInput";
import {
  logVoiceInputLatency,
  nowVoiceInputLatencyMs,
} from "@/services/voiceInput/latency";
import {
  startVoiceInputRecorder,
  type VoiceInputRecorder,
} from "@/services/voiceInput/recorder";
import type {
  ServerVoiceInputEvent,
  VoiceInputSnapshot,
  VoiceInputTranscript,
} from "@/services/voiceInput/types";
import { canStartVoiceInput, isVoiceInputLive } from "./voiceInputStateMachine";

interface VoiceInputState extends VoiceInputSnapshot {
  prewarm: (reason?: string) => Promise<void>;
  start: () => Promise<void>;
  stop: (reason?: "manual" | "send") => Promise<void>;
  cancel: () => Promise<void>;
  resetError: () => void;
}

const VOICE_INPUT_PREWARM_MIN_INTERVAL_MS = 5_000;
const VOICE_INPUT_AVAILABILITY_TIMEOUT_MS = 2_000;
const VOICE_INPUT_START_TIMEOUT_MS = 8_000;
const VOICE_INPUT_STOP_FALLBACK_MS = 2_500;

let recorder: VoiceInputRecorder | null = null;
let connection: VoiceInputConnection | null = null;
let activeToken = 0;
let transcriptSeq = 0;
let queuedAudio: ArrayBuffer[] = [];
let queuedAudioBytes = 0;
let activeStartMs = 0;
let firstAudioChunkLogged = false;
let firstLocalVolumeLogged = false;
let firstTranscriptLogged = false;
let prewarmInFlight: Promise<void> | null = null;
let lastPrewarmRequestAt = 0;
let stopFallbackTimeout: ReturnType<typeof setTimeout> | null = null;
let availabilityAbortController: AbortController | null = null;

function makeTraceId(): string {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return `voice-input-${randomUuid}`;
  return `voice-input-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function makePrewarmTraceId(): string {
  return makeTraceId().replace("voice-input-", "voice-input-prewarm-");
}

function nowMs(): number {
  return Date.now();
}

function initialSnapshot(): VoiceInputSnapshot {
  return {
    phase: "idle",
    traceId: null,
    volumeLevel: 0,
    latestTranscript: null,
    error: null,
    errorCode: null,
    startedAt: null,
    lastEventAt: null,
  };
}

function idleAfterStopSnapshot(): Partial<VoiceInputSnapshot> {
  return {
    phase: "idle",
    traceId: null,
    volumeLevel: 0,
    error: null,
    errorCode: null,
    startedAt: null,
    lastEventAt: nowMs(),
  };
}

function idleAfterFailureSnapshot(message: string, code: string): Partial<VoiceInputSnapshot> {
  return {
    phase: "idle",
    traceId: null,
    volumeLevel: 0,
    latestTranscript: null,
    error: message,
    errorCode: code,
    startedAt: null,
    lastEventAt: nowMs(),
  };
}

function clearStopFallbackTimeout(): void {
  if (!stopFallbackTimeout) return;
  globalThis.clearTimeout(stopFallbackTimeout);
  stopFallbackTimeout = null;
}

function abortAvailabilityCheck(): void {
  availabilityAbortController?.abort();
  availabilityAbortController = null;
}

class VoiceInputStartTimeoutError extends Error {
  constructor() {
    super("语音输入启动超时，请稍后再试");
    this.name = "VoiceInputStartTimeoutError";
  }
}

function isVoiceInputStartTimeoutError(error: unknown): error is VoiceInputStartTimeoutError {
  return error instanceof VoiceInputStartTimeoutError;
}

async function cleanupLocalResources(): Promise<void> {
  clearStopFallbackTimeout();
  abortAvailabilityCheck();
  const currentRecorder = recorder;
  recorder = null;
  queuedAudio = [];
  queuedAudioBytes = 0;
  connection?.close();
  connection = null;
  await currentRecorder?.stop().catch(() => {});
}

function toErrorMessage(error: unknown): string {
  if (isVoiceInputStartTimeoutError(error)) return error.message;
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "没有拿到麦克风权限";
  }
  if (error instanceof Error && error.message) return error.message;
  return "语音输入暂时不可用";
}

function prewarmFailure(
  status: Awaited<ReturnType<typeof prewarmVoiceInput>>,
): { message: string; code: string } | null {
  if (status.status === "unavailable") {
    return {
      message: status.message ?? "语音输入识别服务还没有接上，请稍后再试",
      code: "asr_provider_unavailable",
    };
  }
  if (status.status === "disabled") {
    return {
      message: status.message ?? "语音输入还没有启用",
      code: "voice_input_disabled",
    };
  }
  if (status.status === "error") {
    return {
      message: status.message ?? "语音输入服务暂时不可用，请稍后再试",
      code: "voice_input_prewarm_error",
    };
  }
  return null;
}

export const useVoiceInputStore = create<VoiceInputState>((set, get) => {
  const stopRecorderInBackground = (target: VoiceInputRecorder | null) => {
    void target?.stop().catch((error) => {
      console.warn("[voice-input] recorder stop failed", error);
    });
  };

  const scheduleStopFallback = (token: number) => {
    clearStopFallbackTimeout();
    stopFallbackTimeout = globalThis.setTimeout(() => {
      stopFallbackTimeout = null;
      if (token !== activeToken || get().phase !== "stopping") return;
      const state = get();
      logVoiceInputLatency("frontend_stop_fallback", {
        traceId: state.traceId,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
      activeToken = token + 1;
      void cleanupLocalResources().then(() => {
        set(idleAfterStopSnapshot());
      });
    }, VOICE_INPUT_STOP_FALLBACK_MS);
  };

  const fail = async (token: number, message: string, code: string) => {
    if (token !== activeToken) return;
    const state = get();
    logVoiceInputLatency("frontend_error", {
      traceId: state.traceId,
      phase: state.phase,
      code,
      elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
    });
    activeToken = token + 1;
    await cleanupLocalResources();
    if (activeToken !== token + 1) return;
    set(idleAfterFailureSnapshot(message, code));
  };

  const ensureVoiceInputAvailable = async (token: number, traceId: string) => {
    const controller = new AbortController();
    availabilityAbortController = controller;
    const startedMs = nowVoiceInputLatencyMs();
    const timeoutId = globalThis.setTimeout(() => {
      controller.abort();
    }, VOICE_INPUT_AVAILABILITY_TIMEOUT_MS);

    logVoiceInputLatency("frontend_availability_check_start", { traceId });
    try {
      const result = await prewarmVoiceInput({
        traceId,
        reason: "start",
        signal: controller.signal,
      });
      logVoiceInputLatency("frontend_availability_check_result", {
        traceId,
        status: result.status,
        ttlMs: result.ttlMs,
        warmAgeMs: result.warmAgeMs,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - startedMs),
      });
      if (token !== activeToken) return false;
      const failure = prewarmFailure(result);
      if (failure) {
        await fail(token, failure.message, failure.code);
        return false;
      }
      return true;
    } catch (error) {
      if (token !== activeToken) return false;
      const aborted =
        error instanceof DOMException && error.name === "AbortError";
      logVoiceInputLatency("frontend_availability_check_error", {
        traceId,
        code: aborted
          ? "voice_input_availability_timeout"
          : "voice_input_availability_failed",
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - startedMs),
      });
      await fail(
        token,
        aborted
          ? "语音输入服务响应超时，请稍后再试"
          : "语音输入服务暂时不可用，请稍后再试",
        aborted
          ? "voice_input_availability_timeout"
          : "voice_input_availability_failed",
      );
      return false;
    } finally {
      globalThis.clearTimeout(timeoutId);
      if (availabilityAbortController === controller) {
        availabilityAbortController = null;
      }
    }
  };

  const startRecorderWithTimeout = (
    token: number,
    traceId: string,
    options: Parameters<typeof startVoiceInputRecorder>[0],
  ): Promise<VoiceInputRecorder> => {
    let timedOut = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const deadlineMs = nowVoiceInputLatencyMs() + VOICE_INPUT_START_TIMEOUT_MS;
    const markTimedOut = () => {
      if (timedOut) return;
      timedOut = true;
      logVoiceInputLatency("frontend_recorder_start_timeout", {
        traceId,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
    };
    const recorderPromise = Promise.resolve()
      .then(() => startVoiceInputRecorder(options))
      .then((nextRecorder) => {
        if (!timedOut && nowVoiceInputLatencyMs() >= deadlineMs) {
          markTimedOut();
        }
        if (timedOut || token !== activeToken) {
          stopRecorderInBackground(nextRecorder);
          if (timedOut) throw new VoiceInputStartTimeoutError();
          throw new Error("voice input recorder start was cancelled");
        }
        return nextRecorder;
      });
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = globalThis.setTimeout(() => {
        markTimedOut();
        reject(new VoiceInputStartTimeoutError());
      }, VOICE_INPUT_START_TIMEOUT_MS);
    });
    return Promise.race([recorderPromise, timeoutPromise]).finally(() => {
      if (timeoutId) globalThis.clearTimeout(timeoutId);
    });
  };

  const handleServerEvent = async (token: number, event: ServerVoiceInputEvent) => {
    if (token !== activeToken) return;
    if (event.type === "ready") {
      logVoiceInputLatency("frontend_server_ready", {
        traceId: event.traceId,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
      set({ lastEventAt: nowMs() });
      return;
    }
    if (event.type === "partial" || event.type === "final") {
      if (!firstTranscriptLogged) {
        firstTranscriptLogged = true;
        logVoiceInputLatency("frontend_first_transcript", {
          traceId: event.traceId,
          kind: event.type,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
          textLen: event.text.length,
        });
      }
      if (event.type === "final") {
        logVoiceInputLatency("frontend_final_transcript", {
          traceId: event.traceId,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
          textLen: event.text.length,
        });
      }
      transcriptSeq += 1;
      const latestTranscript: VoiceInputTranscript = {
        kind: event.type,
        traceId: event.traceId,
        text: event.text,
        seq: transcriptSeq,
      };
      set({ latestTranscript, lastEventAt: nowMs() });
      return;
    }
    if (event.type === "stopped") {
      logVoiceInputLatency("frontend_stopped", {
        traceId: event.traceId,
        reason: event.reason,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
      await cleanupLocalResources();
      if (token !== activeToken) return;
      set(idleAfterStopSnapshot());
      return;
    }
    if (event.type === "error") {
      await fail(token, event.message, event.code);
    }
  };

  return {
    ...initialSnapshot(),

    async prewarm(reason = "composer_active") {
      if (isVoiceInputLive(get().phase)) return;
      const now = nowMs();
      if (prewarmInFlight) return prewarmInFlight;
      if (now - lastPrewarmRequestAt < VOICE_INPUT_PREWARM_MIN_INTERVAL_MS) return;

      lastPrewarmRequestAt = now;
      const traceId = makePrewarmTraceId();
      const startedMs = nowVoiceInputLatencyMs();
      logVoiceInputLatency("frontend_prewarm_request_start", { traceId, reason });

      const request = prewarmVoiceInput({ traceId, reason })
        .then((result) => {
          logVoiceInputLatency("frontend_prewarm_result", {
            traceId,
            reason,
            status: result.status,
            ttlMs: result.ttlMs,
            warmAgeMs: result.warmAgeMs,
            elapsedMs: Math.round(nowVoiceInputLatencyMs() - startedMs),
          });
        })
        .catch((error) => {
          logVoiceInputLatency("frontend_prewarm_error", {
            traceId,
            reason,
            message: error instanceof Error ? error.message : String(error),
            elapsedMs: Math.round(nowVoiceInputLatencyMs() - startedMs),
          });
        })
        .finally(() => {
          if (prewarmInFlight === request) prewarmInFlight = null;
        });
      prewarmInFlight = request;
      await request;
    },

    async start() {
      const state = get();
      if (!canStartVoiceInput(state.phase)) return;
      const token = activeToken + 1;
      activeToken = token;
      const traceId = makeTraceId();
      activeStartMs = nowVoiceInputLatencyMs();
      firstAudioChunkLogged = false;
      firstLocalVolumeLogged = false;
      firstTranscriptLogged = false;
      queuedAudioBytes = 0;
      logVoiceInputLatency("frontend_request_start", { traceId });
      set({
        phase: "connecting",
        traceId,
        volumeLevel: 0,
        latestTranscript: null,
        error: null,
        errorCode: null,
        startedAt: nowMs(),
        lastEventAt: nowMs(),
      });

      try {
        if (!(await ensureVoiceInputAvailable(token, traceId))) return;
        if (token !== activeToken) return;
        set({ phase: "requesting_microphone", lastEventAt: nowMs() });
        logVoiceInputLatency("frontend_recorder_start", { traceId });
        const nextRecorder = await startRecorderWithTimeout(token, traceId, {
          onChunk(chunk) {
            if (token !== activeToken) return;
            if (!firstAudioChunkLogged) {
              firstAudioChunkLogged = true;
              logVoiceInputLatency("frontend_first_audio_chunk", {
                traceId,
                elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
                bytesLen: chunk.byteLength,
                queued: !connection,
              });
            }
            if (connection) {
              connection.sendAudio(chunk);
            } else {
              queuedAudio.push(chunk);
              queuedAudioBytes += chunk.byteLength;
            }
          },
          onVolume(volumeLevel) {
            if (token === activeToken && volumeLevel > 0 && !firstLocalVolumeLogged) {
              firstLocalVolumeLogged = true;
              logVoiceInputLatency("frontend_first_local_volume", {
                traceId,
                elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
                volumeLevel,
              });
            }
            if (token === activeToken) set({ volumeLevel });
          },
        });
        logVoiceInputLatency("frontend_recorder_ready", {
          traceId,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
          format: nextRecorder.audio.format,
          sampleRate: nextRecorder.audio.sampleRate,
          channels: nextRecorder.audio.channels,
        });
        recorder = nextRecorder;
        set({ phase: "recording", lastEventAt: nowMs() });
        logVoiceInputLatency("frontend_ready_to_speak", {
          traceId,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
        });
        logVoiceInputLatency("frontend_ws_connect_start", {
          traceId,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
        });
        const nextConnection = await connectVoiceInput({
          traceId,
          audio: nextRecorder.audio,
          onEvent: (event) => void handleServerEvent(token, event),
          onClose: () => {
            if (token !== activeToken) return;
            const phase = get().phase;
            if (phase === "stopping") {
              void cleanupLocalResources().then(() => {
                if (token === activeToken) {
                  set(idleAfterStopSnapshot());
                }
              });
              return;
            }
            if (phase === "recording" || phase === "connecting") {
              void fail(token, "语音输入连接断开了", "voice_input_connection_closed");
            }
          },
          onError: (error) => {
            console.warn("[voice-input] websocket error", error);
          },
        });
        if (token !== activeToken) {
          nextConnection.close();
          return;
        }
        connection = nextConnection;
        logVoiceInputLatency("frontend_ws_open", {
          traceId,
          elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
          queuedChunks: queuedAudio.length,
          queuedBytes: queuedAudioBytes,
        });
        for (const chunk of queuedAudio) {
          connection.sendAudio(chunk);
        }
        if (queuedAudio.length > 0) {
          logVoiceInputLatency("frontend_queued_audio_flushed", {
            traceId,
            elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
            queuedChunks: queuedAudio.length,
            queuedBytes: queuedAudioBytes,
          });
        }
        queuedAudio = [];
        queuedAudioBytes = 0;
      } catch (error) {
        if (token !== activeToken) return;
        await fail(
          token,
          toErrorMessage(error),
          isVoiceInputStartTimeoutError(error)
            ? "voice_input_start_timeout"
            : "voice_input_start_failed",
        );
      }
    },

    async stop(reason: "manual" | "send" = "manual") {
      const state = get();
      if (!isVoiceInputLive(state.phase)) return;
      const token = activeToken;
      logVoiceInputLatency("frontend_stop_request", {
        traceId: state.traceId,
        reason,
        phase: state.phase,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
      set({ phase: "stopping", volumeLevel: 0, lastEventAt: nowMs() });
      if (!connection) {
        activeToken = token + 1;
        void cleanupLocalResources();
        set(idleAfterStopSnapshot());
        return;
      }
      scheduleStopFallback(token);
      connection.stop();
      stopRecorderInBackground(recorder);
      recorder = null;
    },

    async cancel() {
      const state = get();
      if (!isVoiceInputLive(state.phase)) return;
      logVoiceInputLatency("frontend_cancel", {
        traceId: state.traceId,
        phase: state.phase,
        elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
      });
      const token = activeToken + 1;
      activeToken = token;
      connection?.cancel();
      await cleanupLocalResources();
      set({ ...initialSnapshot() });
    },

    resetError() {
      const state = get();
      if (state.phase !== "error" && !state.error && !state.errorCode) return;
      if (state.phase === "error") {
        set({ ...initialSnapshot() });
        return;
      }
      set({ error: null, errorCode: null, lastEventAt: nowMs() });
    },
  };
});
