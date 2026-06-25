import { beforeEach, describe, expect, it, vi } from "vitest";

const browserGlobals = vi.hoisted(() => {
  class TestCustomEvent<T = unknown> {
    detail: T;

    constructor(_type: string, init?: { detail?: T }) {
      this.detail = init?.detail as T;
    }
  }

  const testWindow = {
    __AGENT_FRIEND_SETTINGS__: undefined as unknown,
    dispatchEvent: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };

  globalThis.window = testWindow as unknown as Window & typeof globalThis;
  globalThis.CustomEvent = TestCustomEvent as unknown as typeof CustomEvent;

  return { testWindow };
});

import { DEFAULT_SETTINGS } from "@/lib/settings";
import { voiceApi } from "@/services/api/voice";
import { useVoiceStore } from "./voice";

vi.mock("@tauri-apps/api/event", () => ({
  emit: vi.fn(),
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

vi.mock("@/utils/tauri", () => ({
  isTauri: () => false,
}));

vi.mock("@/services/api/voice", () => ({
  voiceApi: {
    startCall: vi.fn(),
    startAgent: vi.fn(),
    stopCall: vi.fn(),
  },
  voiceErrorMessage: (error: unknown) => String(error),
}));

function resetVoiceStore(): void {
  useVoiceStore.setState({
    phase: "idle",
    callId: null,
    sessionId: null,
    startedAt: null,
    durationMs: 0,
    volumeLevel: 0,
    error: null,
    diagnostic: null,
    isOwner: false,
    pendingStart: null,
    activeChatSessionId: null,
  });
}

describe("voice store", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    browserGlobals.testWindow.__AGENT_FRIEND_SETTINGS__ = {
      ...DEFAULT_SETTINGS,
      voiceTunnelConsentAccepted: false,
    };
    resetVoiceStore();
  });

  it("拨号前读取 settings 快照，未同意公网穿透时进入确认态", async () => {
    await useVoiceStore.getState().requestStart({ sessionId: "session-1" });

    expect(useVoiceStore.getState().phase).toBe("confirming_tunnel");
    expect(useVoiceStore.getState().isOwner).toBe(true);
    expect(useVoiceStore.getState().pendingStart).toEqual({
      sessionId: "session-1",
    });
    expect(voiceApi.startCall).not.toHaveBeenCalled();
  });
});
