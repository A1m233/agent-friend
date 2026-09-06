import { describe, expect, it, vi } from "vitest";
import type { CancelRunResponse, RunSnapshot } from "@/types/run";
import { createRunCancellation } from "./runCancellation";

const run = (version = 1, runtimeId: string | null = "runtime-a"): RunSnapshot => ({
  run_id: "run-a", session_id: "session-a", runtime_instance_id: runtimeId,
  status: "running", outcome: null, reason_code: "", channel: "text", origin: "ag_ui",
  control_scope: "desktop_text", created_at: "2026-09-06T00:00:00Z", started_at: null,
  finished_at: null, version, input_accepted: false,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function setup(storage?: () => Storage | undefined) {
  const cancel = vi.fn<(...args: [string, string, string?]) => Promise<CancelRunResponse>>();
  const snapshot = vi.fn(); const accepted = vi.fn(); const changed = vi.fn();
  const coordinator = createRunCancellation({ cancel, snapshot, accepted, changed, storage: storage ?? (() => undefined) });
  return { coordinator, cancel, snapshot, accepted, changed };
}

describe("run cancellation intent", () => {
  it("retains pre-registration 404 and retries once when registration is observed", async () => {
    const { coordinator, cancel, changed } = setup();
    cancel.mockRejectedValueOnce({ code: "run_not_found" })
      .mockResolvedValueOnce({ result: "accepted", run: run(2) });
    await expect(coordinator.request(run(0, null))).resolves.toBe("pending");
    expect(changed).toHaveBeenLastCalledWith({ "run-a": "requesting" });
    coordinator.observe(run(1));
    await coordinator.retry();
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(cancel).toHaveBeenLastCalledWith("session-a", "run-a", "runtime-a");
    expect(changed).toHaveBeenLastCalledWith({ "run-a": "accepted" });
  });

  it("remembers a registration arriving during a failed cancellation flight without polling", async () => {
    const gate = deferred<CancelRunResponse>();
    const { coordinator, cancel } = setup();
    cancel.mockReturnValueOnce(gate.promise).mockRejectedValue({ code: "network" });
    const pending = coordinator.request(run(0, null));
    coordinator.observe(run(1));
    // A wrong response also leaves intent pending; the registration supplies one retry.
    gate.resolve({ result: "accepted", run: { ...run(1), session_id: "wrong" } });
    await pending;
    await Promise.resolve();
    expect(cancel).toHaveBeenCalledTimes(2);
    await Promise.resolve();
    expect(cancel).toHaveBeenCalledTimes(2);
  });

  it("terminal and runtime changes fence late cancellation responses", async () => {
    const gate = deferred<CancelRunResponse>();
    const { coordinator, cancel, snapshot, accepted, changed } = setup();
    cancel.mockReturnValueOnce(gate.promise);
    const pending = coordinator.request(run());
    coordinator.setRuntime("runtime-b");
    gate.resolve({ result: "accepted", run: run(2) });
    await pending;
    expect(snapshot).not.toHaveBeenCalled(); expect(accepted).not.toHaveBeenCalled();
    expect(changed).toHaveBeenLastCalledWith({});
  });

  it("does not apply an unrelated session response or clear intent on a network failure", async () => {
    const { coordinator, cancel, snapshot } = setup();
    cancel.mockResolvedValueOnce({ result: "accepted", run: { ...run(), session_id: "other" } })
      .mockRejectedValueOnce({ code: "network" });
    await coordinator.request(run()); await coordinator.retry();
    expect(coordinator.has("run-a")).toBe(true);
    expect(snapshot).not.toHaveBeenCalled();
    coordinator.observe({ ...run(3), status: "terminal", outcome: "cancelled" });
    expect(coordinator.has("run-a")).toBe(false);
  });

  it("restores only minimal window-scoped identity and cancellation phase", async () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } as Storage;
    const first = setup(() => storage);
    first.cancel.mockRejectedValueOnce({ code: "network" });
    await first.coordinator.request(run());
    const serialized = [...values.values()][0];
    expect(JSON.parse(serialized)).toEqual([{
      runId: "run-a", sessionId: "session-a", runtimeId: "runtime-a", phase: "requesting",
    }]);
    const second = setup(() => storage);
    second.coordinator.restore();
    expect(second.coordinator.has("run-a")).toBe(true);
    expect(second.changed).toHaveBeenLastCalledWith({ "run-a": "requesting" });
    second.coordinator.setRuntime("runtime-b");
    expect(values.size).toBe(0);
  });
});
