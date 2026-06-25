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
const EVENT_CONFIRM_TUNNEL_CONSENT = "voice://confirm-tunnel-consent";
const EVENT_CANCEL_TUNNEL_CONSENT = "voice://cancel-tunnel-consent";
const EVENT_SNAPSHOT_REQUEST = "voice://snapshot-request";
const EVENT_ACTIVE_CHAT_SESSION = "voice://active-chat-session";

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
  resetError: () => void;
  applyRemoteSnapshot: (snapshot: VoiceCallSnapshot) => void;
  noteActiveChatSession: (sessionId: string | null) => void;
}

let rtcClient: RtcClient | null = null;
let durationTimer: ReturnType<typeof setInterval> | null = null;
let stateSubscriberStarted = false;
let ownerCommandSubscriberStarted = false;

function initialSnapshot(): VoiceCallSnapshot {
  return {
    phase: "idle",
    callId: null,
    sessionId: null,
    startedAt: null,
    durationMs: 0,
    volumeLevel: 0,
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

  const cleanupRtc = async () => {
    const current = rtcClient;
    rtcClient = null;
    await current?.cleanup().catch(() => {});
  };

  const startInternal = async (req: StartVoiceCallRequest) => {
    setAndPublish({
      isOwner: true,
      pendingStart: null,
      phase: "dialing",
      callId: null,
      sessionId: null,
      startedAt: null,
      durationMs: 0,
      volumeLevel: 0,
      error: null,
      diagnostic: null,
    });

    await focusVoiceCallWindow();
    let callId: string | null = null;
    let stage: VoiceCallFailureStage = "start_call";

    try {
      const call = await voiceApi.startCall({ ...req, deferStart: true });
      callId = call.callId;
      setAndPublish({
        phase: "joining_room",
        callId: call.callId,
        sessionId: call.sessionId,
      });

      stage = "rtc_join_publish";
      const { createVolcRtcClient } = await import("@/services/voice/rtcClient");
      rtcClient = createVolcRtcClient({
        onVolume: (volumeLevel) => setAndPublish({ volumeLevel }),
        onError: (message) =>
          setAndPublish({
            error: message,
            diagnostic: { stage: "rtc_join_publish", message },
          }),
      });
      await rtcClient.joinAndPublish({
        rtcAppId: call.rtcAppId,
        roomId: call.roomId,
        userId: call.userId,
        token: call.token,
      });

      setAndPublish({ phase: "starting_agent" });
      stage = "start_agent";
      await voiceApi.startAgent(call.callId);

      setAndPublish({
        phase: "active",
        startedAt: Date.now(),
        durationMs: 0,
        diagnostic: null,
      });
      startDurationTimer();
    } catch (error) {
      clearDurationTimer();
      if (callId) await voiceApi.stopCall(callId).catch(() => {});
      await cleanupRtc();
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
      console.info(
        `[voice] requestStart phase=${state.phase} consentAccepted=${consentAccepted}`,
      );

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
      if (get().isOwner) {
        await get().hangUp();
        return;
      }
      await emitEverywhere(EVENT_HANGUP_REQUEST, {});
    },

    async hangUp() {
      const state = get();
      if (phaseIsFinished(state.phase) || state.phase === "stopping") return;

      clearDurationTimer();
      setAndPublish({ phase: "stopping" });
      let diagnostic: VoiceCallDiagnostic | null = null;
      if (state.callId) {
        await voiceApi.stopCall(state.callId).catch((error) => {
          diagnostic = toDiagnostic("stop_call", error);
        });
      }
      await cleanupRtc().catch((error) => {
        diagnostic = diagnostic ?? toDiagnostic("rtc_cleanup", error);
      });
      setAndPublish({
        phase: "ended",
        isOwner: false,
        callId: null,
        startedAt: null,
        durationMs: 0,
        volumeLevel: 0,
        diagnostic,
      });
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
    if (useVoiceStore.getState().isOwner) {
      void useVoiceStore.getState().hangUp();
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
