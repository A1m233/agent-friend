import { emit, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";

import { voiceApi, voiceErrorMessage } from "@/services/api/voice";
import type { RtcClient } from "@/services/voice/rtcClient";
import type {
  StartVoiceCallRequest,
  VoiceCallDiagnostic,
  VoiceCallFailureStage,
  VoiceCallSnapshot,
  VoiceCallPhase,
} from "@/services/voice/types";
import { getSettingsSnapshot, setSetting } from "@/lib/settings";
import { isTauri } from "@/utils/tauri";
import { canStartVoiceCall, isVoiceCallLive } from "./voiceStateMachine";

const EVENT_STATE = "voice://state";
const EVENT_START_REQUEST = "voice://start-request";
const EVENT_HANGUP_REQUEST = "voice://hangup-request";
const EVENT_TOGGLE_MUTE_REQUEST = "voice://toggle-mute-request";
const EVENT_CONFIRM_TUNNEL_CONSENT = "voice://confirm-tunnel-consent";
const EVENT_CANCEL_TUNNEL_CONSENT = "voice://cancel-tunnel-consent";
const EVENT_SNAPSHOT_REQUEST = "voice://snapshot-request";
const EVENT_ACTIVE_CHAT_SESSION = "voice://active-chat-session";
const EVENT_SESSIONS_CHANGED = "sessions://changed";

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

class VoiceStartCancelled extends Error {
  constructor() {
    super("voice start cancelled");
  }
}

function initialSnapshot(): VoiceCallSnapshot {
  return {
    phase: "idle",
    callId: null,
    sessionId: null,
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
    callId: state.callId,
    sessionId: state.sessionId,
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
  if (error instanceof Error) return error.message;
  const friendly = error as { code?: unknown; friendly?: unknown };
  const code = typeof friendly.code === "string" ? friendly.code : null;
  const message = typeof friendly.friendly === "string" ? friendly.friendly : null;
  return [code, message].filter(Boolean).join(": ") || "unknown";
}

function toDiagnostic(stage: VoiceCallFailureStage, error: unknown): VoiceCallDiagnostic {
  return { stage, message: diagnosticMessage(error) };
}

function isStartCancelled(token: number): boolean {
  return token !== activeStartToken || cancelledStartToken === token;
}

function throwIfStartCancelled(token: number): void {
  if (isStartCancelled(token)) throw new VoiceStartCancelled();
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
    activeStartToken = startToken;
    cancelledStartToken = null;
    console.info("[voice] startInternal", {
      sessionId: req.sessionId ?? "(new)",
      deferStart: req.deferStart ?? true,
    });
    setAndPublish({
      isOwner: true,
      pendingStart: null,
      phase: "dialing",
      callId: null,
      sessionId: null,
      startedAt: null,
      durationMs: 0,
      volumeLevel: 0,
      muted: false,
      error: null,
      diagnostic: null,
    });

    let callId: string | null = null;
    let localRtcClient: RtcClient | null = null;
    let stage: VoiceCallFailureStage = "start_call";

    try {
      await focusVoiceCallWindow();
      throwIfStartCancelled(startToken);
      const call = await voiceApi.startCall({ ...req, deferStart: true });
      throwIfStartCancelled(startToken);
      callId = call.callId;
      console.info("[voice] startCall ok", {
        callId: call.callId,
        sessionId: call.sessionId,
        state: call.state,
      });
      setAndPublish({
        phase: "joining_room",
        callId: call.callId,
        sessionId: call.sessionId,
      });

      stage = "rtc_join_publish";
      const { createVolcRtcClient } = await import("@/services/voice/rtcClient");
      throwIfStartCancelled(startToken);
      localRtcClient = createVolcRtcClient({
        onVolume: (volumeLevel) => {
          if (isStartCancelled(startToken) || get().muted) return;
          setAndPublish({ volumeLevel });
        },
        onError: (message) => {
          if (isStartCancelled(startToken)) return;
          setAndPublish({
            error: message,
            diagnostic: { stage: "rtc_join_publish", message },
          });
        },
      });
      rtcClient = localRtcClient;
      await localRtcClient.joinAndPublish({
        rtcAppId: call.rtcAppId,
        roomId: call.roomId,
        userId: call.userId,
        token: call.token,
      });
      throwIfStartCancelled(startToken);
      console.info("[voice] rtc join/publish ok", {
        callId: call.callId,
        roomId: call.roomId,
        userId: call.userId,
      });

      setAndPublish({ phase: "starting_agent" });
      stage = "start_agent";
      await voiceApi.startAgent(call.callId);
      throwIfStartCancelled(startToken);
      console.info("[voice] startAgent ok", { callId: call.callId });

      setAndPublish({
        phase: "active",
        startedAt: Date.now(),
        durationMs: 0,
        diagnostic: null,
      });
      startDurationTimer();
    } catch (error) {
      if (error instanceof VoiceStartCancelled) {
        console.info("[voice] start cancelled", { stage, callId });
        clearDurationTimer();
        if (callId) await voiceApi.stopCall(callId).catch(() => {});
        await cleanupRtc(localRtcClient);
        if (callId) await emitEverywhere(EVENT_SESSIONS_CHANGED, {});
        return;
      }
      console.warn("[voice] failed", {
        stage,
        message: diagnosticMessage(error),
      });
      clearDurationTimer();
      if (callId) await voiceApi.stopCall(callId).catch(() => {});
      await cleanupRtc(localRtcClient);
      if (callId) await emitEverywhere(EVENT_SESSIONS_CHANGED, {});
      if (isStartCancelled(startToken)) return;
      setAndPublish({
        phase: "error",
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
      await emitEverywhere(EVENT_START_REQUEST, req);
    },

    async confirmTunnelConsent() {
      if (get().phase !== "confirming_tunnel") return;
      await setSetting("voiceTunnelConsentAccepted", true);
      const pendingStart = get().pendingStart ?? {};
      setAndPublish({
        pendingStart: null,
        phase: "idle",
        diagnostic: null,
      });
      await startInternal(pendingStart);
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
      cancelledStartToken = startToken;
      const callId = state.callId;
      const sessionId = state.sessionId;
      clearDurationTimer();
      setAndPublish({
        phase: "ended",
        isOwner: false,
        callId: null,
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
      if (callId) await emitEverywhere(EVENT_SESSIONS_CHANGED, { sessionId });
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
      setAndPublish({ phase: "idle", error: null, diagnostic: null });
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
}

export async function publishActiveChatSession(sessionId: string | null): Promise<void> {
  await emitEverywhere(EVENT_ACTIVE_CHAT_SESSION, { sessionId });
}
