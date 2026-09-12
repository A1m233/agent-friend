import { afterEach, describe, expect, it, vi } from "vitest";
import { startDprSync } from "./dprSync";

describe("DPR synchronization", () => {
  afterEach(() => vi.useRealTimers());

  it("retries after failure using the current DPR, then stops on cleanup", async () => {
    vi.useFakeTimers();
    let dpr = 1.75;
    const send = vi.fn().mockRejectedValueOnce(new Error("IPC failed")).mockResolvedValue(undefined);
    const stop = startDprSync(() => dpr, send);
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).toHaveBeenCalledTimes(2);
    dpr = 1.25;
    await vi.advanceTimersByTimeAsync(2000);
    expect(send.mock.calls[2][0]).toBe(1.25);
    expect(send.mock.calls[2][1].sequence).toBeGreaterThan(send.mock.calls[0][1].sequence);
    stop();
    await vi.advanceTimersByTimeAsync(6000);
    expect(send).toHaveBeenCalledTimes(3);
  });

  it("keeps ordering across effect restarts with unresolved requests", () => {
    vi.useFakeTimers();
    const send = vi.fn((_dpr: number, _version: { pageStartedAt: number; sequence: number }) => new Promise<unknown>(() => {}));
    const first = startDprSync(() => 1.75, send);
    first();
    const second = startDprSync(() => 1.25, send);
    expect(send.mock.calls[1][1].sequence).toBeGreaterThan(send.mock.calls[0][1].sequence);
    expect(send.mock.calls[1][1].pageStartedAt).toBe(send.mock.calls[0][1].pageStartedAt);
    second();
  });

  it("does not send invalid scale values", async () => {
    vi.useFakeTimers();
    let dpr = NaN;
    const send = vi.fn().mockResolvedValue(undefined);
    const stop = startDprSync(() => dpr, send);
    dpr = 0;
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
    dpr = 1.25;
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).toHaveBeenCalledOnce();
    stop();
  });
});
