import { emit, listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";

import { voiceApi, voiceErrorMessage } from "@/services/api/voice";
import type { RtcClient } from "@/services/voice/rtcClient";
import type { StartVoiceCallRequest, VoiceCallSnapshot, VoiceCallPhase } from "@/services/voice/types";
import { getInitialSettings } from "@/lib/settings";
import { isTauri } from "@/utils/tauri";
import { canStartVoiceCall, isVoiceCallLive } from "./voiceStateMachine";

const EVENT_STATE = "voice://state";
const EVENT_START_REQUEST = "voice://start-request";
const EVENT_HANGUP_REQUEST = "voice://hangup-request";
const EVENT_ACTIVE_CHAT_SESSION = "voice://active-chat-session";

interface VoiceState extends VoiceCallSnapshot {
  isOwner: boolean;
  pendingStart: StartVoiceCallRequest | null;
  activeChatSessionId: string | null;
  requestStart: (req?: StartVoiceCallRequest) => Promise<void>;
  requestStartFromAnyWindow: (req?: StartVoiceCallRequest) => Promise<void>;
  confirmTunnelConsent: () => Promise<void>;
  cancelTunnelConsent: () => void;
  setTunnelConsentAccepted: (next: boolean) => Promise<void>;
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
    consentAccepted: getInitialSettings().voiceTunnelConsentAccepted,
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
    consentAccepted: state.consentAccepted,
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

async function persistConsentAccepted(next: boolean): Promise<void> {
  window.__AGENT_FRIEND_SETTINGS__ = {
    ...getInitialSettings(),
    voiceTunnelConsentAccepted: next,
  };
  if (!isTauri()) return;
  await invoke("set_setting", {
    payload: { key: "voiceTunnelConsentAccepted", value: next },
  });
}

function phaseIsFinished(phase: VoiceCallPhase): boolean {
  return phase === "idle" || phase === "ended" || phase === "error";
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
    });

    await focusVoiceCallWindow();
    let callId: string | null = null;

    try {
      const call = await voiceApi.startCall({ ...req, deferStart: true });
      callId = call.callId;
      setAndPublish({
        phase: "joining_room",
        callId: call.callId,
        sessionId: call.sessionId,
      });

      const { createVolcRtcClient } = await import("@/services/voice/rtcClient");
      rtcClient = createVolcRtcClient({
        onVolume: (volumeLevel) => setAndPublish({ volumeLevel }),
        onError: (message) => setAndPublish({ error: message }),
      });
      await rtcClient.joinAndPublish({
        rtcAppId: call.rtcAppId,
        roomId: call.roomId,
        userId: call.userId,
        token: call.token,
      });

      setAndPublish({ phase: "starting_agent" });
      await voiceApi.startAgent(call.callId);

      setAndPublish({
        phase: "active",
        startedAt: Date.now(),
        durationMs: 0,
      });
      startDurationTimer();
    } catch (error) {
      clearDurationTimer();
      if (callId) await voiceApi.stopCall(callId).catch(() => {});
      await cleanupRtc();
      setAndPublish({
        phase: "error",
        error: error instanceof Error ? error.message : voiceErrorMessage(error),
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

      if (!state.consentAccepted) {
        setAndPublish({
          phase: "confirming_tunnel",
          pendingStart: withSession,
          error: null,
        });
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
      await persistConsentAccepted(true);
      const pendingStart = get().pendingStart ?? {};
      setAndPublish({
        consentAccepted: true,
        pendingStart: null,
        phase: "idle",
      });
      await startInternal(pendingStart);
    },

    cancelTunnelConsent() {
      setAndPublish({
        phase: "idle",
        pendingStart: null,
      });
    },

    async setTunnelConsentAccepted(next) {
      await persistConsentAccepted(next);
      setAndPublish({ consentAccepted: next });
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
      if (state.callId) {
        await voiceApi.stopCall(state.callId).catch(() => {});
      }
      await cleanupRtc();
      setAndPublish({
        phase: "ended",
        isOwner: false,
        callId: null,
        startedAt: null,
        durationMs: 0,
        volumeLevel: 0,
      });
    },

    resetError() {
      if (get().phase !== "error") return;
      setAndPublish({ phase: "idle", error: null });
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
  const onActiveSession = (sessionId: string | null) => {
    useVoiceStore.getState().noteActiveChatSession(sessionId);
  };

  if (isTauri()) {
    void listen<StartVoiceCallRequest>(EVENT_START_REQUEST, (event) => onStart(event.payload));
    void listen(EVENT_HANGUP_REQUEST, onHangUp);
    void listen<{ sessionId: string | null }>(EVENT_ACTIVE_CHAT_SESSION, (event) =>
      onActiveSession(event.payload.sessionId),
    );
  }

  window.addEventListener(EVENT_START_REQUEST, (event) => {
    onStart((event as CustomEvent<StartVoiceCallRequest>).detail ?? {});
  });
  window.addEventListener(EVENT_HANGUP_REQUEST, onHangUp);
  window.addEventListener(EVENT_ACTIVE_CHAT_SESSION, (event) => {
    onActiveSession((event as CustomEvent<{ sessionId: string | null }>).detail?.sessionId ?? null);
  });
}

export async function publishActiveChatSession(sessionId: string | null): Promise<void> {
  await emitEverywhere(EVENT_ACTIVE_CHAT_SESSION, { sessionId });
}
