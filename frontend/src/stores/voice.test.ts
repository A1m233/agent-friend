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

const tauriMocks = vi.hoisted(() => ({
  isTauri: vi.fn(() => false),
}));

const rtcMocks = vi.hoisted(() => {
  const client = {
    preflight: vi.fn(),
    prepare: vi.fn(),
    joinRoom: vi.fn(),
    startAudioCapture: vi.fn(),
    publishAudio: vi.fn(),
    joinAndPublish: vi.fn(),
    setMuted: vi.fn(),
    cleanup: vi.fn(),
  };
  return {
    client,
    createVolcRtcClient: vi.fn(() => client),
  };
});

import { DEFAULT_SETTINGS } from "@/lib/settings";
import { voiceApi } from "@/services/api/voice";
import { activityFromBackendEvent, startVoiceOwnerCommandListener, useVoiceStore } from "./voice";
import { invoke } from "@tauri-apps/api/core";
import { emit } from "@tauri-apps/api/event";

vi.mock("@tauri-apps/api/event", () => ({
  emit: vi.fn(),
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

vi.mock("@/utils/tauri", () => ({
  isTauri: tauriMocks.isTauri,
}));

vi.mock("@/services/api/voice", () => ({
  voiceApi: {
    startCall: vi.fn(),
    startAgent: vi.fn(),
    stopCall: vi.fn(),
    subscribeCallEvents: vi.fn(() => vi.fn()),
  },
  voiceErrorMessage: (error: unknown) => String(error),
}));

vi.mock("@/services/voice/rtcClient", () => ({
  createVolcRtcClient: rtcMocks.createVolcRtcClient,
}));

function resetVoiceStore(): void {
  useVoiceStore.setState({
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
    isOwner: false,
    pendingStart: null,
    activeChatSessionId: null,
  });
}

describe("voice store", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tauriMocks.isTauri.mockReturnValue(false);
    rtcMocks.client.preflight.mockResolvedValue(undefined);
    rtcMocks.client.prepare.mockResolvedValue(undefined);
    rtcMocks.client.joinRoom.mockResolvedValue(undefined);
    rtcMocks.client.startAudioCapture.mockResolvedValue(undefined);
    rtcMocks.client.publishAudio.mockResolvedValue(undefined);
    rtcMocks.client.cleanup.mockResolvedValue(undefined);
    rtcMocks.client.setMuted.mockResolvedValue(undefined);
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

  it("非 owner 窗口发起通话时通过 command 暂存 start intent 并打开语音窗口", async () => {
    tauriMocks.isTauri.mockReturnValue(true);
    vi.mocked(invoke).mockResolvedValue(undefined);
    vi.mocked(emit).mockResolvedValue(undefined);

    await useVoiceStore.getState().requestStartFromAnyWindow({
      sessionId: "session-1",
    });

    expect(invoke).toHaveBeenCalledWith("open_voice_call_with_start", {
      req: { sessionId: "session-1" },
    });
    expect(emit).not.toHaveBeenCalledWith(
      "voice://start-request",
      expect.objectContaining({ sessionId: "session-1" }),
    );
  });

  it("语音窗口 owner ready 后消费 pending start 并进入公网穿透确认", async () => {
    tauriMocks.isTauri.mockReturnValue(true);
    vi.mocked(invoke).mockImplementation(async (command) => {
      if (command === "consume_pending_voice_start") return { sessionId: "session-1" };
      return undefined;
    });

    startVoiceOwnerCommandListener();
    await Promise.resolve();
    await Promise.resolve();

    expect(invoke).toHaveBeenCalledWith("consume_pending_voice_start");
    expect(useVoiceStore.getState().phase).toBe("confirming_tunnel");
    expect(useVoiceStore.getState().pendingStart).toEqual({ sessionId: "session-1" });
    expect(voiceApi.startCall).not.toHaveBeenCalled();
  });

  it("挂断时立即进入结束态，不等待后端断连完成", async () => {
    let resolveStop!: () => void;
    vi.mocked(voiceApi.stopCall).mockReturnValue(
      new Promise<string>((resolve) => {
        resolveStop = () => resolve("ended");
      }),
    );
    useVoiceStore.setState({
      phase: "active",
      callId: "call-1",
      sessionId: "session-1",
      traceId: "trace-1",
      startedAt: Date.now(),
      durationMs: 1200,
      volumeLevel: 42,
      muted: true,
      isOwner: true,
    });

    const hangUp = useVoiceStore.getState().hangUp();

    expect(useVoiceStore.getState().phase).toBe("ended");
    expect(useVoiceStore.getState().callId).toBeNull();
    expect(useVoiceStore.getState().traceId).toBeNull();
    expect(useVoiceStore.getState().volumeLevel).toBe(0);
    expect(useVoiceStore.getState().muted).toBe(false);

    resolveStop();
    await hangUp;
    expect(voiceApi.stopCall).toHaveBeenCalledWith("call-1");
  });

  it("joinRoom 后并行触发 startAgent，不等待 publishAudio 完成", async () => {
    browserGlobals.testWindow.__AGENT_FRIEND_SETTINGS__ = {
      ...DEFAULT_SETTINGS,
      voiceTunnelConsentAccepted: true,
    };
    const order: string[] = [];
    vi.mocked(voiceApi.startCall).mockImplementation(async () => {
      order.push("startCall");
      return {
        callId: "call-1",
        sessionId: "session-1",
        state: "pending",
        rtcAppId: "app",
        roomId: "room",
        userId: "user",
        token: "token",
        traceId: "trace-1",
      };
    });
    vi.mocked(voiceApi.startAgent).mockImplementation(async () => {
      order.push("startAgent");
    });
    rtcMocks.client.preflight.mockImplementation(async () => {
      order.push("preflight");
    });
    rtcMocks.client.prepare.mockImplementation(async () => {
      order.push("prepare");
    });
    rtcMocks.client.joinRoom.mockImplementation(async () => {
      order.push("joinRoom");
    });
    rtcMocks.client.startAudioCapture.mockImplementation(async () => {
      order.push("startAudioCapture");
    });
    rtcMocks.client.publishAudio.mockImplementation(async () => {
      order.push("publishAudio");
    });

    await useVoiceStore.getState().requestStart({ sessionId: "session-1" });

    expect(order).toEqual([
      "preflight",
      "startCall",
      "prepare",
      "joinRoom",
      "startAgent",
      "startAudioCapture",
      "publishAudio",
    ]);
    expect(useVoiceStore.getState().phase).toBe("active");
    expect(useVoiceStore.getState().traceId).toBe("trace-1");
    expect(voiceApi.startCall).toHaveBeenCalledWith(
      expect.objectContaining({ traceId: expect.any(String), deferStart: true }),
    );
  });

  it("用户侧字幕即使未标记 definite 也会把 UI 从播放态切回理解态", () => {
    expect(
      activityFromBackendEvent({
        type: "callback",
        callId: "call-1",
        stage: "subtitle",
        speaker: "user",
        definite: false,
        textLen: 4,
      }),
    ).toBe("user_detected");
  });

  it("空的 bot 字幕不会把 UI 切到播放态", () => {
    expect(
      activityFromBackendEvent({
        type: "callback",
        callId: "call-1",
        stage: "subtitle",
        speaker: "bot",
        definite: true,
        textLen: 0,
      }),
    ).toBeNull();
  });

  it("有内容的 bot 字幕会把 UI 切到播放态", () => {
    expect(
      activityFromBackendEvent({
        type: "callback",
        callId: "call-1",
        stage: "subtitle",
        speaker: "bot",
        definite: true,
        textLen: 4,
      }),
    ).toBe("speaking");
  });
});
