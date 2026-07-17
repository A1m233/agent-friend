import { emit, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";

import { voiceApi, voiceErrorMessage } from "@/services/api/voice";
import type { RtcClient } from "@/services/voice/rtcClient";
import type {
  StartVoiceCallRequest,
  VoiceCallActivity,
  VoiceCallBackendEvent,
  VoiceCallDiagnostic,
  VoiceCallFailureStage,
  VoiceCallSnapshot,
  VoiceCallPhase,
} from "@/services/voice/types";
import { getSettingsSnapshot, setSetting } from "@/lib/settings";
import {
  hasPermissions,
  refreshPermissions,
  subscribePermissionSnapshot,
} from "@/lib/permissions";
import { waitForFeaturePermissions } from "@/lib/permissions/featureGuard";
import { isTauri } from "@/utils/tauri";
import { emitSessionsChanged } from "./sessions";
import { canStartVoiceCall, isVoiceCallLive } from "./voiceStateMachine";

const EVENT_STATE = "voice://state";
const EVENT_START_REQUEST = "voice://start-request";
const EVENT_HANGUP_REQUEST = "voice://hangup-request";
const EVENT_TOGGLE_MUTE_REQUEST = "voice://toggle-mute-request";
const EVENT_CONFIRM_TUNNEL_CONSENT = "voice://confirm-tunnel-consent";
const EVENT_CANCEL_TUNNEL_CONSENT = "voice://cancel-tunnel-consent";
const EVENT_SNAPSHOT_REQUEST = "voice://snapshot-request";
const EVENT_ACTIVE_CHAT_SESSION = "voice://active-chat-session";
const EVENT_WINDOW_SHOWN = "window://shown";

interface VoiceState extends VoiceCallSnapshot {
  isOwner: boolean;
  pendingStart: StartVoiceCallRequest | null;
  activeChatSessionId: string | null;
  requestStart: (req?: StartVoiceCallRequest) => Promise<void>;
  requestStartFromAnyWindow: (req?: StartVoiceCallRequest) => Promise<void>;
  confirmTunnelConsent: () => Promise<void>;
  requestConfirmTunnelConsent: () => Promise<void>;
  cancelTunnelConsent: () => void;
  requestCancelTunnelConsent: () => Promise<void>;
  requestHangUp: () => Promise<void>;
  hangUp: () => Promise<void>;
  requestToggleMute: () => Promise<void>;
  toggleMute: () => Promise<void>;
  resetError: () => void;
  applyRemoteSnapshot: (snapshot: VoiceCallSnapshot) => void;
  noteActiveChatSession: (sessionId: string | null) => void;
}

let rtcClient: RtcClient | null = null;
let durationTimer: ReturnType<typeof setInterval> | null = null;
let stateSubscriberStarted = false;
let ownerCommandSubscriberStarted = false;
let activeStartToken = 0;
let cancelledStartToken: number | null = null;
let callEventsUnsubscribe: (() => void) | null = null;
let speakingFallbackTimer: ReturnType<typeof setTimeout> | null = null;
let permissionIntentAbortController: AbortController | null = null;

class VoiceStartCancelled extends Error {
  constructor() {
    super("voice start cancelled");
  }
}

function initialSnapshot(): VoiceCallSnapshot {
  return {
    phase: "idle",
    activity: "idle",
    callId: null,
    sessionId: null,
    traceId: null,
    startedAt: null,
    durationMs: 0,
    volumeLevel: 0,
    muted: false,
    error: null,
    diagnostic: null,
  };
}

function toSnapshot(state: VoiceState): VoiceCallSnapshot {
  return {
    phase: state.phase,
    activity: state.activity,
    callId: state.callId,
    sessionId: state.sessionId,
    traceId: state.traceId,
    startedAt: state.startedAt,
    durationMs: state.durationMs,
    volumeLevel: state.volumeLevel,
    muted: state.muted,
    error: state.error,
    diagnostic: state.diagnostic,
  };
}

function emitWindowFallback<T>(name: string, payload: T): void {
  window.dispatchEvent(new CustomEvent<T>(name, { detail: payload }));
}

async function emitEverywhere<T>(name: string, payload: T): Promise<void> {
  if (isTauri()) {
    await emit(name, payload).catch(() => {});
  }
  emitWindowFallback(name, payload);
}

function clearDurationTimer(): void {
  if (durationTimer) clearInterval(durationTimer);
  durationTimer = null;
}

function phaseIsFinished(phase: VoiceCallPhase): boolean {
  return phase === "idle" || phase === "ended" || phase === "error";
}

function diagnosticMessage(error: unknown): string {
  if (typeof error === "string" && error.trim()) return error.trim();
  if (error instanceof Error) return error.message;
  const friendly = error as { code?: unknown; friendly?: unknown };
  const code = typeof friendly.code === "string" ? friendly.code : null;
  const message = typeof friendly.friendly === "string" ? friendly.friendly : null;
  return [code, message].filter(Boolean).join(": ") || "unknown";
}

function toDiagnostic(stage: VoiceCallFailureStage, error: unknown): VoiceCallDiagnostic {
  return { stage, message: diagnosticMessage(error) };
}

function makeTraceId(): string {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return randomUuid;
  return `voice-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowMs(): number {
  return globalThis.performance?.now?.() ?? Date.now();
}

function logVoiceLatency(
  event: string,
  fields: Record<string, string | number | boolean | null | undefined>,
): void {
  const parts = Object.entries(fields)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${String(value)}`);
  console.info(`[voice][latency] event=${event}${parts.length ? ` ${parts.join(" ")}` : ""}`);
}

function isStartCancelled(token: number): boolean {
  return token !== activeStartToken || cancelledStartToken === token;
}

function throwIfStartCancelled(token: number): void {
  if (isStartCancelled(token)) throw new VoiceStartCancelled();
}

function closeCallEventStream(): void {
  callEventsUnsubscribe?.();
  callEventsUnsubscribe = null;
}

function clearSpeakingFallbackTimer(): void {
  if (speakingFallbackTimer) clearTimeout(speakingFallbackTimer);
  speakingFallbackTimer = null;
}

export function activityFromBackendEvent(event: VoiceCallBackendEvent): VoiceCallActivity | null {
  if (event.type === "ping") return null;
  const stage = event.stage?.toLowerCase();
  const speaker = event.speaker;
  if (stage === "subtitle") {
    if (speaker === "bot" && (event.textLen ?? 0) > 0) return "speaking";
    if (speaker === "user" && (event.definite || (event.textLen ?? 0) > 0)) {
      return "user_detected";
    }
    return null;
  }
  switch (stage) {
    case "listening":
      return "listening";
    case "thinking":
      return "thinking";
    case "answering":
      return "answering";
    case "answerfinish":
      return "listening";
    default:
      return null;
  }
}

export const useVoiceStore = create<VoiceState>((set, get) => {
  const publish = () => {
    void emitEverywhere(EVENT_STATE, toSnapshot(get()));
  };

  const setAndPublish = (partial: Partial<VoiceState>) => {
    set(partial);
    publish();
  };

  const startDurationTimer = () => {
    clearDurationTimer();
    durationTimer = setInterval(() => {
      const state = get();
      if (!state.startedAt || !isVoiceCallLive(state.phase)) return;
      set({ durationMs: Date.now() - state.startedAt });
      publish();
    }, 1000);
  };

  const cleanupRtc = async (target: RtcClient | null = rtcClient) => {
    if (!target) return;
    if (rtcClient === target) rtcClient = null;
    await target.cleanup().catch(() => {});
  };

  const startInternal = async (req: StartVoiceCallRequest) => {
    const startToken = activeStartToken + 1;
    const traceId = req.traceId ?? makeTraceId();
    const startMs = nowMs();
    activeStartToken = startToken;
    cancelledStartToken = null;
    console.info("[voice] startInternal", {
      sessionId: req.sessionId ?? "(new)",
      deferStart: req.deferStart ?? true,
      traceId,
    });
    logVoiceLatency("frontend_request_start", {
      traceId,
      sessionId: req.sessionId ?? null,
    });
    setAndPublish({
      isOwner: true,
      pendingStart: null,
      phase: "preparing_runtime",
      activity: "idle",
      callId: null,
      sessionId: null,
      traceId,
      startedAt: null,
      durationMs: 0,
      volumeLevel: 0,
      muted: false,
      error: null,
      diagnostic: null,
    });

    let callId: string | null = null;
    let localRtcClient: RtcClient | null = null;
    let stage: VoiceCallFailureStage = "runtime_prepare";
    let activeAnswerRoundId: number | null = null;
    let activeAnswerRoundStartedMs: number | null = null;
    const loggedRemoteVolumeRounds = new Set<number>();

    try {
      await focusVoiceCallWindow();
      throwIfStartCancelled(startToken);
      if (isTauri()) {
        await invoke("prepare_voice_runtime");
        throwIfStartCancelled(startToken);
      }
      stage = "preflight";
      setAndPublish({ phase: "preflighting" });
      const { createVolcRtcClient } = await import("@/services/voice/rtcClient");
      throwIfStartCancelled(startToken);
      localRtcClient = createVolcRtcClient({
        onVolume: (volumeLevel) => {
          if (isStartCancelled(startToken) || get().muted) return;
          if (volumeLevel > 0 && get().volumeLevel <= 0) {
            logVoiceLatency("frontend_first_local_volume", {
              traceId,
              callId,
              elapsedMs: Math.round(nowMs() - startMs),
              volumeLevel,
            });
          }
          setAndPublish({ volumeLevel });
        },
        onRemoteAudioFirstFrame: (event) => {
          if (isStartCancelled(startToken)) return;
          logVoiceLatency("frontend_remote_audio_first_frame", {
            traceId,
            callId,
            elapsedMs: Math.round(nowMs() - startMs),
            remoteUserId: event.userId ?? null,
            isScreen: event.isScreen,
          });
        },
        onRemoteAudioVolume: (event) => {
          if (isStartCancelled(startToken)) return;
          if (activeAnswerRoundId === null) return;
          if (loggedRemoteVolumeRounds.has(activeAnswerRoundId)) return;
          loggedRemoteVolumeRounds.add(activeAnswerRoundId);
          logVoiceLatency("frontend_first_remote_audio_volume", {
            traceId,
            callId,
            roundId: activeAnswerRoundId,
            elapsedMs: Math.round(nowMs() - startMs),
            roundElapsedMs:
              activeAnswerRoundStartedMs === null
                ? null
                : Math.round(nowMs() - activeAnswerRoundStartedMs),
            remoteUserId: event.userId ?? null,
            volumeLevel: event.volume,
          });
        },
        onError: (message) => {
          if (isStartCancelled(startToken)) return;
          setAndPublish({
            error: message,
            diagnostic: { stage, message },
          });
        },
      });
      rtcClient = localRtcClient;
      await localRtcClient.preflight();
      logVoiceLatency("frontend_preflight_ok", {
        traceId,
        elapsedMs: Math.round(nowMs() - startMs),
      });

      setAndPublish({ phase: "dialing" });
      stage = "start_call";
      const call = await voiceApi.startCall({ ...req, deferStart: true, traceId });
      throwIfStartCancelled(startToken);
      callId = call.callId;
      console.info("[voice] startCall ok", {
        callId: call.callId,
        sessionId: call.sessionId,
        state: call.state,
        traceId: call.traceId,
      });
      logVoiceLatency("frontend_start_call_ok", {
        traceId: call.traceId,
        callId: call.callId,
        sessionId: call.sessionId,
        elapsedMs: Math.round(nowMs() - startMs),
      });
      setAndPublish({
        phase: "joining_room",
        activity: "idle",
        callId: call.callId,
        sessionId: call.sessionId,
        traceId: call.traceId,
      });
      closeCallEventStream();
      callEventsUnsubscribe = voiceApi.subscribeCallEvents(
        call.callId,
        (event) => {
          const activity = activityFromBackendEvent(event);
          const current = get();
          if (current.callId !== call.callId || phaseIsFinished(current.phase)) return;

          const stage = event.stage?.toLowerCase();
          const roundId = typeof event.roundId === "number" ? event.roundId : null;
          if (roundId !== null) {
            if (stage === "answering") {
              activeAnswerRoundId = roundId;
              activeAnswerRoundStartedMs = nowMs();
            } else if (
              stage === "subtitle" &&
              event.speaker === "bot" &&
              (event.textLen ?? 0) > 0 &&
              activeAnswerRoundId === null
            ) {
              activeAnswerRoundId = roundId;
              activeAnswerRoundStartedMs = nowMs();
            } else if (stage === "answerfinish" && activeAnswerRoundId === roundId) {
              activeAnswerRoundId = null;
              activeAnswerRoundStartedMs = null;
            }
          }

          if (!activity) return;
          clearSpeakingFallbackTimer();
          setAndPublish({ activity });
          if (activity === "speaking") {
            speakingFallbackTimer = setTimeout(() => {
              const latest = get();
              if (latest.callId === call.callId && latest.phase === "active" && latest.activity === "speaking") {
                setAndPublish({ activity: "listening" });
              }
            }, 2500);
          }
        },
        (error) => {
          console.warn("[voice] call event stream error", { callId: call.callId, error });
        },
      );

      stage = "rtc_join";
      throwIfStartCancelled(startToken);
      await localRtcClient.prepare({
        rtcAppId: call.rtcAppId,
        roomId: call.roomId,
        userId: call.userId,
        token: call.token,
      });
      await localRtcClient.joinRoom();
      throwIfStartCancelled(startToken);
      logVoiceLatency("frontend_rtc_join_ok", {
        traceId: call.traceId,
        callId: call.callId,
        elapsedMs: Math.round(nowMs() - startMs),
      });

      let startAgentDone = false;
      let startAgentError: unknown = null;
      const startAgentStartedMs = nowMs();
      const startAgentPromise = (async () => {
        try {
          logVoiceLatency("frontend_start_agent_request", {
            traceId: call.traceId,
            callId: call.callId,
            elapsedMs: Math.round(nowMs() - startMs),
          });
          await voiceApi.startAgent(call.callId);
          startAgentDone = true;
          logVoiceLatency("frontend_start_agent_ok", {
            traceId: call.traceId,
            callId: call.callId,
            elapsedMs: Math.round(nowMs() - startMs),
            durationMs: Math.round(nowMs() - startAgentStartedMs),
          });
        } catch (error) {
          startAgentError = error;
        }
      })();

      setAndPublish({ phase: "preparing_microphone", activity: "idle" });
      stage = "rtc_capture";
      await localRtcClient.startAudioCapture();
      throwIfStartCancelled(startToken);
      logVoiceLatency("frontend_audio_capture_ok", {
        traceId: call.traceId,
        callId: call.callId,
        elapsedMs: Math.round(nowMs() - startMs),
      });

      stage = "rtc_publish";
      await localRtcClient.publishAudio();
      throwIfStartCancelled(startToken);
      console.info("[voice] rtc join/capture/publish ok", {
        callId: call.callId,
        roomId: call.roomId,
        userId: call.userId,
      });
      logVoiceLatency("frontend_rtc_publish_ok", {
        traceId: call.traceId,
        callId: call.callId,
        elapsedMs: Math.round(nowMs() - startMs),
      });

      if (!startAgentDone) setAndPublish({ phase: "connecting_agent", activity: "idle" });
      stage = "start_agent";
      await startAgentPromise;
      if (startAgentError) throw startAgentError;
      throwIfStartCancelled(startToken);
      console.info("[voice] startAgent ok", { callId: call.callId });

      logVoiceLatency("frontend_ready_to_talk", {
        traceId: call.traceId,
        callId: call.callId,
        sessionId: call.sessionId,
        elapsedMs: Math.round(nowMs() - startMs),
      });
      setAndPublish({
        phase: "active",
        activity: "listening",
        startedAt: Date.now(),
        durationMs: 0,
        diagnostic: null,
      });
      startDurationTimer();
    } catch (error) {
      if (error instanceof VoiceStartCancelled) {
        console.info("[voice] start cancelled", { stage, callId });
        clearDurationTimer();
        closeCallEventStream();
        clearSpeakingFallbackTimer();
        if (callId) await voiceApi.stopCall(callId).catch(() => {});
        await cleanupRtc(localRtcClient);
        if (isTauri()) await invoke("stop_voice_tunnel").catch(() => {});
        if (callId) await emitSessionsChanged({ action: "voice-call-ended" });
        return;
      }
      console.warn("[voice] failed", {
        stage,
        message: diagnosticMessage(error),
      });
      if (stage === "rtc_capture" || stage === "rtc_publish") {
        void refreshPermissions("manual");
      }
      clearDurationTimer();
      closeCallEventStream();
      clearSpeakingFallbackTimer();
      if (callId) await voiceApi.stopCall(callId).catch(() => {});
      await cleanupRtc(localRtcClient);
      if (isTauri()) await invoke("stop_voice_tunnel").catch(() => {});
      if (callId) await emitSessionsChanged({ action: "voice-call-failed" });
      if (isStartCancelled(startToken)) return;
      setAndPublish({
        phase: "error",
        activity: "idle",
        error: error instanceof Error ? error.message : voiceErrorMessage(error),
        diagnostic: toDiagnostic(stage, error),
      });
    }
  };

  return {
    ...initialSnapshot(),
    isOwner: false,
    pendingStart: null,
    activeChatSessionId: null,

    async requestStart(req = {}) {
      const state = get();
      if (!canStartVoiceCall(state.phase)) {
        await focusVoiceCallWindow();
        return;
      }

      const withSession = {
        ...req,
        sessionId: req.sessionId ?? state.activeChatSessionId ?? undefined,
      };

      if (!hasPermissions(["microphone"])) {
        permissionIntentAbortController?.abort();
        const controller = new AbortController();
        permissionIntentAbortController = controller;
        const requestId = `voice-call-${makeTraceId()}`;
        setAndPublish({
          isOwner: true,
          phase: "waiting_permission_flow",
          activity: "idle",
          pendingStart: withSession,
          error: null,
          diagnostic: null,
        });
        await focusVoiceCallWindow();
        void waitForFeaturePermissions(
          {
            requestId,
            reason: "feature_fallback",
            permissionIds: ["microphone"],
            sourceWindow: "voice-call",
          },
          controller.signal,
        ).then(
          (granted) => {
            if (permissionIntentAbortController === controller) {
              permissionIntentAbortController = null;
            }
            const current = get();
            if (
              controller.signal.aborted ||
              current.phase !== "waiting_permission_flow" ||
              current.pendingStart !== withSession
            ) {
              return;
            }
            if (!granted) {
              setAndPublish({
                isOwner: false,
                phase: "error",
                pendingStart: null,
                error: "需要先处理麦克风权限",
              });
              return;
            }
            setAndPublish({ phase: "idle", pendingStart: null });
            void get().requestStart(withSession);
          },
          (error) => {
            const current = get();
            if (controller.signal.aborted || current.phase !== "waiting_permission_flow") return;
            permissionIntentAbortController = null;
            setAndPublish({
              isOwner: false,
              phase: "error",
              pendingStart: null,
              error: error instanceof Error ? error.message : String(error),
            });
          },
        );
        return;
      }

      const consentAccepted = getSettingsSnapshot().voiceTunnelConsentAccepted;
      console.info("[voice] requestStart", {
        phase: state.phase,
        consentAccepted,
        reqSessionId: req.sessionId ?? null,
        activeChatSessionId: state.activeChatSessionId,
        resolvedSessionId: withSession.sessionId ?? null,
      });

      if (!consentAccepted) {
        setAndPublish({
          isOwner: true,
          phase: "confirming_tunnel",
          activity: "idle",
          pendingStart: withSession,
          error: null,
          diagnostic: null,
        });
        await focusVoiceCallWindow();
        return;
      }

      await startInternal(withSession);
    },

    async requestStartFromAnyWindow(req = {}) {
      if (get().isOwner || !isTauri()) {
        await get().requestStart(req);
        return;
      }
      await openVoiceCallWithStart(req);
    },

    async confirmTunnelConsent() {
      if (get().phase !== "confirming_tunnel") return;
      await setSetting("voiceTunnelConsentAccepted", true);
      const pendingStart = get().pendingStart ?? {};
      setAndPublish({
        pendingStart: null,
        phase: "idle",
        activity: "idle",
        diagnostic: null,
      });
      await get().requestStart(pendingStart);
    },

    async requestConfirmTunnelConsent() {
      if (get().isOwner || !isTauri()) {
        await get().confirmTunnelConsent();
        return;
      }
      await emitEverywhere(EVENT_CONFIRM_TUNNEL_CONSENT, {});
    },

    cancelTunnelConsent() {
      if (get().phase !== "confirming_tunnel") return;
      setAndPublish({
        isOwner: false,
        phase: "idle",
        activity: "idle",
        pendingStart: null,
        diagnostic: null,
      });
    },

    async requestCancelTunnelConsent() {
      if (get().isOwner || !isTauri()) {
        get().cancelTunnelConsent();
        return;
      }
      await emitEverywhere(EVENT_CANCEL_TUNNEL_CONSENT, {});
    },

    async requestHangUp() {
      const state = get();
      console.info("[voice][request] hangup", {
        isOwner: state.isOwner,
        phase: state.phase,
        callId: state.callId,
      });
      if (state.isOwner) {
        await get().hangUp();
        return;
      }
      console.info("[voice][request] hangup emit-owner", {
        phase: state.phase,
        callId: state.callId,
      });
      await emitEverywhere(EVENT_HANGUP_REQUEST, {});
    },

    async hangUp() {
      const state = get();
      console.info("[voice][owner] hangUp invoked", {
        phase: state.phase,
        callId: state.callId,
        muted: state.muted,
        hasRtc: Boolean(rtcClient),
      });
      if (phaseIsFinished(state.phase) || state.phase === "stopping") {
        console.info("[voice][owner] hangUp ignored", {
          phase: state.phase,
          callId: state.callId,
        });
        return;
      }

      const startToken = activeStartToken;
      permissionIntentAbortController?.abort();
      permissionIntentAbortController = null;
      cancelledStartToken = startToken;
      const callId = state.callId;
      const sessionId = state.sessionId;
      clearDurationTimer();
      closeCallEventStream();
      clearSpeakingFallbackTimer();
      setAndPublish({
        phase: "ended",
        activity: "idle",
        isOwner: false,
        pendingStart: null,
        callId: null,
        traceId: null,
        startedAt: null,
        durationMs: 0,
        volumeLevel: 0,
        muted: false,
        diagnostic: null,
      });
      console.info("[voice][owner] hangUp optimistic-ended", {
        callId,
        previousPhase: state.phase,
      });

      let diagnostic: VoiceCallDiagnostic | null = null;
      if (callId) {
        await voiceApi.stopCall(callId).catch((error) => {
          diagnostic = toDiagnostic("stop_call", error);
        });
      }
      await cleanupRtc().catch((error) => {
        diagnostic = diagnostic ?? toDiagnostic("rtc_cleanup", error);
      });
      if (isTauri()) {
        await invoke("stop_voice_tunnel").catch((error) => {
          diagnostic = diagnostic ?? toDiagnostic("stop_call", error);
        });
      }
      if (callId) await emitSessionsChanged({ sessionId, action: "voice-call-stopped" });
      if (diagnostic && activeStartToken === startToken && get().phase === "ended") {
        setAndPublish({ diagnostic });
      }
      console.info("[voice][owner] hangUp cleanup done", {
        callId,
        diagnostic,
      });
    },

    async requestToggleMute() {
      const state = get();
      console.info("[voice][request] toggle-mute", {
        isOwner: state.isOwner,
        phase: state.phase,
        callId: state.callId,
        muted: state.muted,
        hasRtc: Boolean(rtcClient),
      });
      if (state.isOwner || !isTauri()) {
        await get().toggleMute();
        return;
      }
      console.info("[voice][request] toggle-mute emit-owner", {
        phase: state.phase,
        callId: state.callId,
        muted: state.muted,
      });
      await emitEverywhere(EVENT_TOGGLE_MUTE_REQUEST, {});
    },

    async toggleMute() {
      const state = get();
      console.info("[voice][owner] toggleMute invoked", {
        phase: state.phase,
        callId: state.callId,
        muted: state.muted,
        hasRtc: Boolean(rtcClient),
      });
      if (!isVoiceCallLive(state.phase) || !rtcClient) {
        console.info("[voice][owner] toggleMute ignored", {
          phase: state.phase,
          callId: state.callId,
          hasRtc: Boolean(rtcClient),
        });
        return;
      }

      const nextMuted = !state.muted;
      setAndPublish({
        muted: nextMuted,
        volumeLevel: nextMuted ? 0 : state.volumeLevel,
        diagnostic: null,
      });
      console.info("[voice][owner] toggleMute optimistic", {
        callId: state.callId,
        nextMuted,
      });

      let failed = false;
      await rtcClient.setMuted(nextMuted).catch((error) => {
        failed = true;
        const diagnostic = toDiagnostic("rtc_mute", error);
        console.warn("[voice][owner] toggleMute rtc-set failed", {
          callId: state.callId,
          nextMuted,
          diagnostic,
        });
        setAndPublish({
          muted: state.muted,
          volumeLevel: state.muted ? 0 : get().volumeLevel,
          diagnostic,
        });
      });
      if (!failed) {
        console.info("[voice][owner] toggleMute rtc-set done", {
          callId: state.callId,
          muted: nextMuted,
        });
      }
    },

    resetError() {
      if (get().phase !== "error") return;
      setAndPublish({
        phase: "idle",
        activity: "idle",
        traceId: null,
        error: null,
        diagnostic: null,
      });
    },

    applyRemoteSnapshot(snapshot) {
      if (get().isOwner) return;
      set({ ...snapshot });
    },

    noteActiveChatSession(sessionId) {
      set({ activeChatSessionId: sessionId });
    },
  };
});

export async function focusVoiceCallWindow(): Promise<void> {
  if (!isTauri()) return;
  await invoke("open_voice_call").catch(() => {});
}

subscribePermissionSnapshot(() => {
  if (hasPermissions(["microphone"])) return;
  const state = useVoiceStore.getState();
  if (
    state.phase !== "waiting_permission_flow" &&
    state.phase !== "confirming_tunnel" &&
    isVoiceCallLive(state.phase)
  ) {
    void state.hangUp();
  }
});

async function openVoiceCallWithStart(req: StartVoiceCallRequest): Promise<void> {
  if (!isTauri()) return;
  await invoke("open_voice_call_with_start", { req }).catch(() => {});
}

async function consumePendingVoiceStart(): Promise<void> {
  if (!isTauri()) return;
  const pendingStart = await invoke<StartVoiceCallRequest | null>(
    "consume_pending_voice_start",
  ).catch(() => null);
  if (!pendingStart) return;
  await useVoiceStore.getState().requestStart(pendingStart);
}

export function startVoiceStateSubscriber(): void {
  if (stateSubscriberStarted) return;
  stateSubscriberStarted = true;

  if (isTauri()) {
    void listen<VoiceCallSnapshot>(EVENT_STATE, (event) => {
      useVoiceStore.getState().applyRemoteSnapshot(event.payload);
    });
  }
  window.addEventListener(EVENT_STATE, (event) => {
    const payload = (event as CustomEvent<VoiceCallSnapshot>).detail;
    if (payload) useVoiceStore.getState().applyRemoteSnapshot(payload);
  });
  void emitEverywhere(EVENT_SNAPSHOT_REQUEST, {});
}

export function startVoiceOwnerCommandListener(): void {
  if (ownerCommandSubscriberStarted) return;
  ownerCommandSubscriberStarted = true;

  const onStart = (payload: StartVoiceCallRequest) => {
    void useVoiceStore.getState().requestStart(payload);
  };
  const onHangUp = () => {
    const state = useVoiceStore.getState();
    console.info("[voice][owner-listener] hangup event", {
      isOwner: state.isOwner,
      phase: state.phase,
      callId: state.callId,
    });
    if (state.isOwner) {
      void useVoiceStore.getState().hangUp();
    } else {
      console.info("[voice][owner-listener] hangup ignored", {
        phase: state.phase,
        callId: state.callId,
      });
    }
  };
  const onToggleMute = () => {
    const state = useVoiceStore.getState();
    console.info("[voice][owner-listener] toggle-mute event", {
      isOwner: state.isOwner,
      phase: state.phase,
      callId: state.callId,
      muted: state.muted,
    });
    if (state.isOwner) {
      void useVoiceStore.getState().toggleMute();
    } else {
      console.info("[voice][owner-listener] toggle-mute ignored", {
        phase: state.phase,
        callId: state.callId,
      });
    }
  };
  const onConfirmTunnelConsent = () => {
    void useVoiceStore.getState().confirmTunnelConsent();
  };
  const onCancelTunnelConsent = () => {
    useVoiceStore.getState().cancelTunnelConsent();
  };
  const onSnapshotRequest = () => {
    void emitEverywhere(EVENT_STATE, toSnapshot(useVoiceStore.getState()));
  };
  const onActiveSession = (sessionId: string | null) => {
    useVoiceStore.getState().noteActiveChatSession(sessionId);
  };

  if (isTauri()) {
    void listen<StartVoiceCallRequest>(EVENT_START_REQUEST, (event) => onStart(event.payload));
    void listen(EVENT_WINDOW_SHOWN, () => {
      void consumePendingVoiceStart();
    });
    void listen(EVENT_HANGUP_REQUEST, onHangUp);
    void listen(EVENT_TOGGLE_MUTE_REQUEST, onToggleMute);
    void listen(EVENT_CONFIRM_TUNNEL_CONSENT, onConfirmTunnelConsent);
    void listen(EVENT_CANCEL_TUNNEL_CONSENT, onCancelTunnelConsent);
    void listen(EVENT_SNAPSHOT_REQUEST, onSnapshotRequest);
    void listen<{ sessionId: string | null }>(EVENT_ACTIVE_CHAT_SESSION, (event) =>
      onActiveSession(event.payload.sessionId),
    );
  }

  window.addEventListener(EVENT_START_REQUEST, (event) => {
    onStart((event as CustomEvent<StartVoiceCallRequest>).detail ?? {});
  });
  window.addEventListener(EVENT_HANGUP_REQUEST, onHangUp);
  window.addEventListener(EVENT_TOGGLE_MUTE_REQUEST, onToggleMute);
  window.addEventListener(EVENT_CONFIRM_TUNNEL_CONSENT, onConfirmTunnelConsent);
  window.addEventListener(EVENT_CANCEL_TUNNEL_CONSENT, onCancelTunnelConsent);
  window.addEventListener(EVENT_SNAPSHOT_REQUEST, onSnapshotRequest);
  window.addEventListener(EVENT_ACTIVE_CHAT_SESSION, (event) => {
    onActiveSession((event as CustomEvent<{ sessionId: string | null }>).detail?.sessionId ?? null);
  });
  void consumePendingVoiceStart();
}

export async function publishActiveChatSession(sessionId: string | null): Promise<void> {
  await emitEverywhere(EVENT_ACTIVE_CHAT_SESSION, { sessionId });
}
