import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerVoiceInputEvent } from "@/services/voiceInput/types";

const voiceInputMocks = vi.hoisted(() => {
  const connection = {
    sendAudio: vi.fn(),
    stop: vi.fn(),
    cancel: vi.fn(),
    close: vi.fn(),
  };
  const recorder = {
    audio: {
      format: "pcm16" as const,
      sampleRate: 16_000,
      channels: 1 as const,
    },
    stop: vi.fn().mockResolvedValue(undefined),
  };
  return {
    connection,
    recorder,
    connectVoiceInput: vi.fn().mockResolvedValue(connection),
    prewarmVoiceInput: vi.fn().mockResolvedValue({
      status: "started",
      traceId: "trace-1",
      ttlMs: 30_000,
    }),
    startVoiceInputRecorder: vi.fn().mockResolvedValue(recorder),
    logVoiceInputLatency: vi.fn(),
  };
});

vi.mock("@/services/api/voiceInput", () => ({
  connectVoiceInput: voiceInputMocks.connectVoiceInput,
  prewarmVoiceInput: voiceInputMocks.prewarmVoiceInput,
}));

vi.mock("@/services/voiceInput/recorder", () => ({
  startVoiceInputRecorder: voiceInputMocks.startVoiceInputRecorder,
}));

vi.mock("@/services/voiceInput/latency", () => ({
  logVoiceInputLatency: voiceInputMocks.logVoiceInputLatency,
  nowVoiceInputLatencyMs: () => Date.now(),
}));

import { useVoiceInputStore } from "./voiceInput";

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function resetVoiceInputStore(): void {
  useVoiceInputStore.setState({
    phase: "idle",
    traceId: null,
    volumeLevel: 0,
    latestTranscript: null,
    error: null,
    errorCode: null,
    startedAt: null,
    lastEventAt: null,
  });
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("voice input store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    vi.clearAllMocks();
    voiceInputMocks.recorder.stop.mockResolvedValue(undefined);
    voiceInputMocks.connectVoiceInput.mockResolvedValue(voiceInputMocks.connection);
    voiceInputMocks.startVoiceInputRecorder.mockResolvedValue(voiceInputMocks.recorder);
    resetVoiceInputStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("falls back to idle when stopping gets no server acknowledgement", async () => {
    await useVoiceInputStore.getState().start();

    expect(useVoiceInputStore.getState().phase).toBe("recording");

    await useVoiceInputStore.getState().stop("manual");

    expect(voiceInputMocks.connection.stop).toHaveBeenCalledTimes(1);
    expect(useVoiceInputStore.getState().phase).toBe("stopping");

    await vi.advanceTimersByTimeAsync(2_500);

    expect(voiceInputMocks.connection.close).toHaveBeenCalledTimes(1);
    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().traceId).toBeNull();
  });

  it("cancels an in-flight recorder start without opening the websocket", async () => {
    const recorderStart = deferred<typeof voiceInputMocks.recorder>();
    voiceInputMocks.startVoiceInputRecorder.mockReturnValueOnce(recorderStart.promise);

    const startPromise = useVoiceInputStore.getState().start();

    await flushMicrotasks();

    expect(useVoiceInputStore.getState().phase).toBe("requesting_microphone");

    await useVoiceInputStore.getState().stop("manual");

    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().traceId).toBeNull();

    recorderStart.resolve(voiceInputMocks.recorder);
    await startPromise;

    expect(voiceInputMocks.recorder.stop).toHaveBeenCalledTimes(1);
    expect(voiceInputMocks.connectVoiceInput).not.toHaveBeenCalled();
    expect(useVoiceInputStore.getState().phase).toBe("idle");
  });

  it("returns to idle when recorder startup times out", async () => {
    const recorderStart = deferred<typeof voiceInputMocks.recorder>();
    voiceInputMocks.startVoiceInputRecorder.mockReturnValueOnce(recorderStart.promise);

    const startPromise = useVoiceInputStore.getState().start();

    await flushMicrotasks();

    expect(useVoiceInputStore.getState().phase).toBe("requesting_microphone");

    await vi.advanceTimersByTimeAsync(8_000);
    await startPromise;

    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().traceId).toBeNull();
    expect(useVoiceInputStore.getState().errorCode).toBe("voice_input_start_timeout");
    expect(voiceInputMocks.connectVoiceInput).not.toHaveBeenCalled();

    recorderStart.resolve(voiceInputMocks.recorder);
    await flushMicrotasks();

    expect(voiceInputMocks.recorder.stop).toHaveBeenCalledTimes(1);
  });

  it("does not start the recorder when the ASR provider is unavailable", async () => {
    voiceInputMocks.prewarmVoiceInput.mockResolvedValueOnce({
      status: "unavailable",
      traceId: "trace-1",
      ttlMs: 30_000,
      message: "语音输入识别服务还没有接上，请稍后再试",
    });

    await useVoiceInputStore.getState().start();

    expect(voiceInputMocks.startVoiceInputRecorder).not.toHaveBeenCalled();
    expect(voiceInputMocks.connectVoiceInput).not.toHaveBeenCalled();
    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().errorCode).toBe("asr_provider_unavailable");
  });

  it("times out a synchronously slow recorder startup before opening the websocket", async () => {
    voiceInputMocks.startVoiceInputRecorder.mockImplementationOnce(() => {
      vi.setSystemTime(Date.now() + 9_000);
      return Promise.resolve(voiceInputMocks.recorder);
    });

    await useVoiceInputStore.getState().start();

    expect(voiceInputMocks.recorder.stop).toHaveBeenCalledTimes(1);
    expect(voiceInputMocks.connectVoiceInput).not.toHaveBeenCalled();
    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().errorCode).toBe("voice_input_start_timeout");
    expect(voiceInputMocks.logVoiceInputLatency).toHaveBeenCalledWith(
      "frontend_recorder_start_timeout",
      expect.objectContaining({ elapsedMs: 9_000 }),
    );
  });

  it("keeps the composer interactive after ASR unavailable errors", async () => {
    let capturedServerEventHandler = false;
    let emitServerEvent: (event: ServerVoiceInputEvent) => void = () => {
      throw new Error("voice input test did not capture onEvent");
    };
    voiceInputMocks.connectVoiceInput.mockImplementationOnce(async (options) => {
      capturedServerEventHandler = true;
      emitServerEvent = options.onEvent;
      return voiceInputMocks.connection;
    });

    await useVoiceInputStore.getState().start();

    expect(useVoiceInputStore.getState().phase).toBe("recording");
    expect(capturedServerEventHandler).toBe(true);

    emitServerEvent({
      type: "error",
      traceId: useVoiceInputStore.getState().traceId ?? "trace-1",
      code: "asr_provider_unavailable",
      message: "语音输入识别服务还没有接上，请稍后再试",
    });
    await flushMicrotasks();

    expect(voiceInputMocks.connection.close).toHaveBeenCalledTimes(1);
    expect(useVoiceInputStore.getState().phase).toBe("idle");
    expect(useVoiceInputStore.getState().traceId).toBeNull();
    expect(useVoiceInputStore.getState().errorCode).toBe("asr_provider_unavailable");
  });
});
