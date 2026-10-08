import { beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));

describe("history observations", () => {
  beforeEach(() => { vi.resetModules(); invoke.mockClear(); });

  it("correlates phases, deduplicates StrictMode and stops after finish", async () => {
    const { startHistoryObservation, recordHistoryPhase } = await import("./historyObservation");
    const read = startHistoryObservation("session-a", "latest");
    read.phase("projection-ready", { eventCount: 80 });
    read.phase("projection-ready", { eventCount: 80 });
    recordHistoryPhase(read.navigationId, "frame-ready", { messageCount: 5 });
    read.finish("success");
    recordHistoryPhase(read.navigationId, "state-published");
    read.finish("error");
    const rows = invoke.mock.calls.map(([, payload]) => payload.observation);
    expect(rows.map((row) => row.phase)).toEqual(["started", "projection-ready", "frame-ready", "finished"]);
    expect(new Set(rows.map((row) => row.navigationId))).toEqual(new Set([read.navigationId]));
    expect(rows.at(-1).status).toBe("success");
  });

  it("bounds abandoned reads and ignores accidental extra fields", async () => {
    const { startHistoryObservation, recordHistoryPhase } = await import("./historyObservation");
    const first = startHistoryObservation("session-a", "latest");
    for (let i = 0; i < 128; i++) startHistoryObservation("session-b", "before");
    const lastCount = invoke.mock.calls.length;
    recordHistoryPhase(first.navigationId, "frame-ready");
    expect(invoke).toHaveBeenCalledTimes(lastCount);
    const read = startHistoryObservation("session-c", "content");
    const details = { eventCount: 2, accidentalText: "private prompt" };
    read.phase("projection-ready", details);
    expect(JSON.stringify(invoke.mock.calls)).not.toContain("private prompt");
  });

  it("does not fail history when native logging is unavailable", async () => {
    invoke.mockRejectedValueOnce(new Error("native logger unavailable"));
    const { startHistoryObservation } = await import("./historyObservation");
    expect(() => startHistoryObservation("session-a", "latest").finish("error")).not.toThrow();
    await Promise.resolve();
  });
});
