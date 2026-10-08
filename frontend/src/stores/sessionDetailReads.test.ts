import { describe, expect, it, vi } from "vitest";
import type { SessionDetail } from "@/types/meta";
import { createSessionDetailReads } from "./sessionDetailReads";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
const detail = (sessionId: string, revision: number): SessionDetail => ({
  session_id: sessionId, title: "test", persona: "test", model: "test", events: [], revision,
});

describe("session detail read coordination", () => {
  it("shares same-session requests and keeps other sessions independent", async () => {
    const first = deferred<SessionDetail>();
    const load = vi.fn((id: string) => id === "a" ? first.promise : Promise.resolve(detail(id, 1)));
    const reads = createSessionDetailReads(load);
    const left = reads.read("a");
    expect(reads.read("a")).toBe(left);
    await expect(reads.read("b")).resolves.toEqual(detail("b", 1));
    first.resolve(detail("a", 1));
    await expect(left).resolves.toEqual(detail("a", 1));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("delivers the first valid response before scheduling a dirty refresh", async () => {
    const first = deferred<SessionDetail>();
    const refresh = vi.fn();
    const reads = createSessionDetailReads(() => first.promise, refresh);
    const request = reads.read("a");
    reads.invalidate("a"); reads.invalidate("a");
    first.resolve(detail("a", 1));
    await expect(request).resolves.toEqual(detail("a", 1));
    expect(refresh).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledExactlyOnceWith("a"));
  });

  it("only aborts transport when the final subscriber leaves", async () => {
    const response = deferred<SessionDetail>();
    const load = vi.fn((_id: string, _signal: AbortSignal) => response.promise);
    const reads = createSessionDetailReads(load);
    const left = new AbortController(); const right = new AbortController();
    const a = reads.read("a", left.signal); const b = reads.read("a", right.signal);
    left.abort();
    await expect(a).rejects.toMatchObject({ name: "AbortError" });
    expect(load.mock.calls[0][1].aborted).toBe(false);
    right.abort();
    await expect(b).rejects.toMatchObject({ name: "AbortError" });
    expect(load.mock.calls[0][1].aborted).toBe(true);
    response.resolve(detail("a", 1));
  });

  it("A to B to A creates a new request and late completion cannot release it", async () => {
    const old = deferred<SessionDetail>(); const fresh = deferred<SessionDetail>();
    const load = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const reads = createSessionDetailReads(load);
    const first = new AbortController(); const next = new AbortController();
    const stale = reads.read("a", first.signal);
    first.abort(); await expect(stale).rejects.toMatchObject({ name: "AbortError" });
    const current = reads.read("a", next.signal);
    old.resolve(detail("a", 1));
    await Promise.resolve();
    expect(load.mock.calls[1][1].aborted).toBe(false);
    fresh.resolve(detail("a", 2));
    await expect(current).resolves.toEqual(detail("a", 2));
  });

  it("failed and mismatched responses release entries for explicit retry", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(detail("other", 1)).mockResolvedValueOnce(detail("a", 2));
    const reads = createSessionDetailReads(load);
    await expect(reads.read("a")).rejects.toThrow("offline");
    await expect(reads.read("a")).rejects.toThrow("identity mismatch");
    await expect(reads.read("a")).resolves.toEqual(detail("a", 2));
  });
});