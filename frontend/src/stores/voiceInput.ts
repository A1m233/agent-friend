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

async function cleanupLocalResources(): Promise<void> {
  const currentRecorder = recorder;
  recorder = null;
  queuedAudio = [];
  queuedAudioBytes = 0;
  connection?.close();
  connection = null;
  await currentRecorder?.stop().catch(() => {});
}

function toErrorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "没有拿到麦克风权限";
  }
  if (error instanceof Error && error.message) return error.message;
  return "语音输入暂时不可用";
}

export const useVoiceInputStore = create<VoiceInputState>((set, get) => {
  const fail = async (token: number, message: string, code: string) => {
    if (token !== activeToken) return;
    const state = get();
    logVoiceInputLatency("frontend_error", {
      traceId: state.traceId,
      phase: state.phase,
      code,
      elapsedMs: Math.round(nowVoiceInputLatencyMs() - activeStartMs),
    });
    await cleanupLocalResources();
    set({
      phase: "error",
      volumeLevel: 0,
      error: message,
      errorCode: code,
      lastEventAt: nowMs(),
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
      set({
        phase: "idle",
        traceId: null,
        volumeLevel: 0,
        error: null,
        errorCode: null,
        startedAt: null,
        lastEventAt: nowMs(),
      });
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
        phase: "requesting_microphone",
        traceId,
        volumeLevel: 0,
        latestTranscript: null,
        error: null,
        errorCode: null,
        startedAt: nowMs(),
        lastEventAt: nowMs(),
      });

      try {
        logVoiceInputLatency("frontend_recorder_start", { traceId });
        const nextRecorder = await startVoiceInputRecorder({
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
        if (token !== activeToken) {
          await nextRecorder.stop();
          return;
        }
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
                  set({
                    phase: "idle",
                    traceId: null,
                    volumeLevel: 0,
                    error: null,
                    errorCode: null,
                    startedAt: null,
                    lastEventAt: nowMs(),
                  });
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
        await fail(token, toErrorMessage(error), "voice_input_start_failed");
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
        await cleanupLocalResources();
        set({ phase: "idle", traceId: null, startedAt: null, lastEventAt: nowMs() });
        return;
      }
      await recorder?.stop().catch(() => {});
      recorder = null;
      connection.stop();
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
      if (get().phase !== "error") return;
      set({ ...initialSnapshot() });
    },
  };
});
