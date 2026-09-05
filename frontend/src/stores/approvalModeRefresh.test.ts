import { describe, expect, it, vi } from "vitest";
import type { SessionApprovalModeState } from "@/services/api/sessions";
import { createApprovalModeRefresher } from "./approvalModeRefresh";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function state(sessionId: string, version: number): SessionApprovalModeState {
  return { session_id: sessionId, approval_mode: "session_auto", approval_mode_version: version };
}

function setup() {
  let scope = { sessionId: "a", openToken: 1 };
  const requests: ReturnType<typeof deferred<SessionApprovalModeState>>[] = [];
  const load = vi.fn(() => {
    const request = deferred<SessionApprovalModeState>();
    requests.push(request);
    return request.promise;
  });
  const apply = vi.fn();
  const refresher = createApprovalModeRefresher({ current: () => scope, load, apply });
  return {
    ...refresher, requests, load, apply,
    navigate(sessionId: string) { scope = { sessionId, openToken: scope.openToken + 1 }; },
  };
}

describe("approval mode refresh ownership", () => {
  it("shares one GET for concurrent reads", async () => {
    const runtime = setup();
    const first = runtime.refresh("a");
    const second = runtime.refresh("a");
    expect(runtime.load).toHaveBeenCalledTimes(1);
    runtime.requests[0].resolve(state("a", 1));
    expect(await Promise.all([first, second])).toEqual(["applied", "applied"]);
    expect(runtime.apply).toHaveBeenCalledTimes(1);
  });

  it("coalesces invalidations and follows changes during the trailing read", async () => {
    const runtime = setup();
    const readers = [runtime.refresh("a")];
    for (let index = 0; index < 5; index += 1) {
      readers.push(runtime.refresh("a", { invalidate: true }));
    }
    expect(runtime.load).toHaveBeenCalledTimes(1);
    runtime.requests[0].resolve(state("a", 1));
    await Promise.resolve();
    expect(runtime.load).toHaveBeenCalledTimes(2);
    expect(runtime.apply).not.toHaveBeenCalled();
    readers.push(runtime.refresh("a", { invalidate: true }));
    runtime.requests[1].resolve(state("a", 2));
    await Promise.resolve();
    expect(runtime.load).toHaveBeenCalledTimes(3);
    expect(runtime.apply).not.toHaveBeenCalled();
    runtime.requests[2].resolve(state("a", 3));
    expect(await Promise.all(readers)).toEqual(readers.map(() => "applied"));
    expect(runtime.apply).toHaveBeenCalledExactlyOnceWith(state("a", 3));
  });

  it("does not retry a failure without new invalidation and releases the slot", async () => {
    const runtime = setup();
    const first = runtime.refresh("a");
    runtime.requests[0].reject(new Error("offline"));
    expect(await first).toBe("failed");
    expect(runtime.load).toHaveBeenCalledTimes(1);
    const retry = runtime.refresh("a");
    runtime.requests[1].resolve(state("a", 2));
    expect(await retry).toBe("applied");
  });

  it("preserves invalidation received before a failed GET settles", async () => {
    const runtime = setup();
    const first = runtime.refresh("a");
    const changed = runtime.refresh("a", { invalidate: true });
    runtime.requests[0].reject(new Error("offline"));
    await Promise.resolve();
    expect(runtime.load).toHaveBeenCalledTimes(2);
    runtime.requests[1].resolve(state("a", 3));
    expect(await Promise.all([first, changed])).toEqual(["applied", "applied"]);
  });

  it("keeps sessions independent and rejects a mismatched response identity", async () => {
    const runtime = setup();
    expect(await runtime.refresh("b")).toBe("stale");
    const first = runtime.refresh("a");
    runtime.navigate("b");
    const second = runtime.refresh("b");
    expect(runtime.load.mock.calls).toEqual([["a"], ["b"]]);
    runtime.requests[0].resolve(state("a", 4));
    runtime.requests[1].resolve(state("a", 5));
    expect(await Promise.all([first, second])).toEqual(["stale", "failed"]);
    expect(runtime.apply).not.toHaveBeenCalled();
  });

  it("reuses the pending A request after A -> B -> A but rejects its old scope", async () => {
    const runtime = setup();
    const oldA = runtime.refresh("a");
    runtime.navigate("b");
    runtime.navigate("a");
    const newA = runtime.refresh("a");
    expect(runtime.load).toHaveBeenCalledTimes(1);
    runtime.requests[0].resolve(state("a", 1));
    await Promise.resolve();
    expect(runtime.load).toHaveBeenCalledTimes(2);
    expect(runtime.apply).not.toHaveBeenCalled();
    runtime.requests[1].resolve(state("a", 2));
    expect(await oldA).toBe("stale");
    expect(await newA).toBe("applied");
    expect(runtime.apply).toHaveBeenCalledExactlyOnceWith(state("a", 2));
  });

  it("fences stopped subscribers and permits a new generation to refresh", async () => {
    const runtime = setup();
    const old = runtime.refresh("a");
    runtime.invalidate();
    const resumed = runtime.refresh("a");
    runtime.requests[0].resolve(state("a", 1));
    await Promise.resolve();
    runtime.requests[1].resolve(state("a", 2));
    expect(await old).toBe("stale");
    expect(await resumed).toBe("applied");
    expect(runtime.apply).toHaveBeenCalledExactlyOnceWith(state("a", 2));
  });
});
