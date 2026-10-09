import { describe, expect, it, vi } from "vitest";
import type { SessionDetail } from "@/types/meta";
import { createSessionDetailReads } from "./sessionDetailReads";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const detail = (sessionId: string, revision: number): SessionDetail => ({
  session_id: sessionId, title: "test", persona: "test", model: "test", events: [], revision,
});

describe("session detail read coordination", () => {
  it("shares all same-session readers while another session remains independent", async () => {
    const first = deferred<SessionDetail>();
    const load = vi.fn((id: string) => id === "a" ? first.promise : Promise.resolve(detail(id, 1)));
    const reads = createSessionDetailReads(load);
    const left = reads.read("a");
    const right = reads.read("a");
    expect(left).toBe(right);
    await expect(reads.read("b")).resolves.toEqual(detail("b", 1));
    expect(load.mock.calls.map(([id]) => id)).toEqual(["a", "b"]);
    first.resolve(detail("a", 1));
    await expect(left).resolves.toEqual(detail("a", 1));
  });

  it("content changes during a GET coalesce into one trailing read before any reader resolves", async () => {
    const first = deferred<SessionDetail>();
    const trailing = deferred<SessionDetail>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(trailing.promise);
    const reads = createSessionDetailReads(load);
    const left = reads.read("a");
    const settled = vi.fn();
    void left.then(settled);
    reads.invalidate("a"); reads.invalidate("a");
    const right = reads.read("a");
    first.resolve(detail("a", 1));
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(2);
    expect(settled).not.toHaveBeenCalled();
    trailing.resolve(detail("a", 3));
    await expect(Promise.all([left, right])).resolves.toEqual([detail("a", 3), detail("a", 3)]);
  });

  it("A to B to A lifecycle changes keep one A GET and demand a fresh trailing response", async () => {
    const first = deferred<SessionDetail>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(detail("a", 2));
    const reads = createSessionDetailReads(load);
    const old = reads.read("a");
    reads.invalidateLifecycle(); reads.invalidateLifecycle();
    const fresh = reads.read("a");
    expect(load).toHaveBeenCalledTimes(1);
    first.resolve(detail("a", 1));
    await expect(fresh).resolves.toEqual(detail("a", 2));
    await old;
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("makes progress under continuous invalidation and delivers the final quiet version in a later batch", async () => {
    const first = deferred<SessionDetail>();
    const second = deferred<SessionDetail>();
    const last = deferred<SessionDetail>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(last.promise);
    const delivered: number[] = [];
    let next: Promise<SessionDetail> | undefined;
    const reads = createSessionDetailReads(load, (id) => {
      next = reads.read(id);
      void next.then((value) => { delivered.push(value.revision!); });
    });
    const initial = reads.read("a");
    reads.invalidate("a");
    first.resolve(detail("a", 1));
    await Promise.resolve();
    reads.invalidate("a");
    second.resolve(detail("a", 2));
    await expect(initial).resolves.toEqual(detail("a", 2));
    expect(load).toHaveBeenCalledTimes(3);
    last.resolve(detail("a", 3));
    await next;
    expect(delivered).toEqual([3]);
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("failed or mismatched reads release the entry for a later explicit retry", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(detail("other", 1)).mockResolvedValueOnce(detail("a", 2));
    const reads = createSessionDetailReads(load);
    await expect(reads.read("a")).rejects.toThrow("offline");
    await expect(reads.read("a")).rejects.toThrow("identity mismatch");
    await expect(reads.read("a")).resolves.toEqual(detail("a", 2));
  });
});
