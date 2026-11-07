import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sessionsApi, type SessionComposerState } from "@/services/api/sessions";
import { openComposerSession, resetComposerSession, retryComposerPreparation, useComposerSessionStore } from "./composerSession";

vi.mock("@/services/api/sessions", () => ({ sessionsApi: {
  composer: vi.fn(), prepare: vi.fn(), preparation: vi.fn(),
  cancelPreparation: vi.fn(async () => ({ state: "idle" })),
} }));

const config = (id: string, model = "deepseek/deepseek-chat"): SessionComposerState => ({
  session_id: id, model, reasoningPreset: "none", read_only: false,
  approval_mode: "per_request", approval_mode_version: 0,
});

describe("independent composer lifecycle", () => {
  beforeEach(() => {
    resetComposerSession(); vi.clearAllMocks(); vi.useFakeTimers();
    vi.mocked(sessionsApi.prepare).mockImplementation(async (id) => ({ session_id: id, state: "preparing" }));
    vi.mocked(sessionsApi.preparation).mockImplementation(async (id) => ({ session_id: id, state: "ready" }));
  });
  afterEach(() => { resetComposerSession(); vi.useRealTimers(); });

  it("loads configuration and prepares without issuing any history read", async () => {
    vi.mocked(sessionsApi.composer).mockResolvedValue(config("one"));
    const apply = vi.fn();
    openComposerSession("one", apply);
    await vi.advanceTimersByTimeAsync(0);
    expect(apply).toHaveBeenCalledWith(config("one"));
    expect(useComposerSessionStore.getState().phase).toBe("preparing");
    await vi.advanceTimersByTimeAsync(200);
    expect(useComposerSessionStore.getState().phase).toBe("ready");
  });

  it("ignores old metadata and aborts its request after a session switch", async () => {
    let old!: (value: SessionComposerState) => void;
    vi.mocked(sessionsApi.composer).mockReturnValueOnce(new Promise((resolve) => { old = resolve; }));
    vi.mocked(sessionsApi.composer).mockResolvedValueOnce(config("two"));
    const first = vi.fn(), second = vi.fn();
    openComposerSession("one", first);
    const signal = vi.mocked(sessionsApi.composer).mock.calls[0][1];
    openComposerSession("two", second);
    old(config("one"));
    await vi.advanceTimersByTimeAsync(0);
    expect(signal?.aborted).toBe(true);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith(config("two"));
    expect(sessionsApi.prepare).toHaveBeenCalledTimes(1);
    expect(useComposerSessionStore.getState().sessionId).toBe("two");
  });

  it("preserves configuration on preparation failure and can retry", async () => {
    vi.mocked(sessionsApi.composer).mockResolvedValue(config("one"));
    vi.mocked(sessionsApi.prepare).mockRejectedValueOnce({ friendly: "暂不可用" });
    openComposerSession("one", vi.fn());
    await vi.advanceTimersByTimeAsync(0);
    expect(useComposerSessionStore.getState()).toMatchObject({ phase: "failed", configuration: config("one") });
    retryComposerPreparation();
    await vi.advanceTimersByTimeAsync(200);
    expect(useComposerSessionStore.getState().phase).toBe("ready");
  });

  it("does not prepare read-only sessions", async () => {
    vi.mocked(sessionsApi.composer).mockResolvedValue({ ...config("one"), read_only: true });
    openComposerSession("one", vi.fn());
    await vi.advanceTimersByTimeAsync(0);
    expect(sessionsApi.prepare).not.toHaveBeenCalled();
  });

  it("waits for the previous cancellation before preparing the same session again", async () => {
    vi.mocked(sessionsApi.composer).mockResolvedValue(config("one"));
    openComposerSession("one", vi.fn());
    await vi.advanceTimersByTimeAsync(0);
    let cancelled!: () => void;
    vi.mocked(sessionsApi.cancelPreparation).mockReturnValueOnce(new Promise((resolve) => {
      cancelled = () => resolve({ session_id: "one", state: "idle" });
    }));
    const apply = vi.fn();
    openComposerSession("one", apply);
    await vi.advanceTimersByTimeAsync(0);
    expect(apply).toHaveBeenCalledWith(config("one"));
    expect(sessionsApi.prepare).toHaveBeenCalledTimes(1);
    cancelled();
    await vi.advanceTimersByTimeAsync(0);
    expect(sessionsApi.prepare).toHaveBeenCalledTimes(2);
  });
});
