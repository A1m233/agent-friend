import { describe, expect, it } from "vitest";
import type { RunSnapshot } from "@/types/run";
import {
  sessionActiveRun,
  sessionRunPresentation,
  shouldShowMessageActionBar,
  type RunPresentationSource,
} from "./runPresentation";

function run(
  runId: string,
  status: RunSnapshot["status"],
  createdAt: string,
  controlScope: string | null = "desktop_text",
): RunSnapshot {
  return {
    run_id: runId,
    session_id: "session-a",
    status,
    outcome: status === "terminal" ? "completed" : null,
    reason_code: "",
    channel: "text",
    origin: "ag_ui",
    control_scope: controlScope,
    created_at: createdAt,
    started_at: status === "running" ? createdAt : null,
    finished_at: status === "terminal" ? createdAt : null,
    version: status === "terminal" ? 3 : status === "running" ? 2 : 1,
  };
}

function source(
  runs: RunSnapshot[],
  phase: RunPresentationSource["runCalibrationPhase"] = "ready",
  stopRequestsByRun: Record<string, unknown> = {},
): RunPresentationSource {
  return {
    runSnapshots: Object.fromEntries(runs.map((item) => [item.run_id, item])),
    runCalibrationPhase: phase,
    stopRequestsByRun,
  };
}

describe("session run presentation", () => {
  it("prefers the running run over queued work and ignores terminal history", () => {
    const queued = run("queued", "queued", "2026-08-28T00:00:00Z");
    const running = run("running", "running", "2026-08-28T00:00:01Z");
    const terminal = run("terminal", "terminal", "2026-08-28T00:00:02Z");

    expect(sessionActiveRun(source([queued, running, terminal]), "session-a")).toBe(running);
  });

  it("uses stable creation order when only queued work exists", () => {
    const later = run("later", "queued", "2026-08-28T00:00:02Z");
    const earlier = run("earlier", "queued", "2026-08-28T00:00:01Z");

    expect(sessionActiveRun(source([later, earlier]), "session-a")).toBe(earlier);
  });

  it("keeps cancellation busy until terminal and removes the stop capability", () => {
    const running = run("running", "running", "2026-08-28T00:00:01Z");

    expect(sessionRunPresentation(source([running], "ready", { running: "accepted" }), "session-a"))
      .toMatchObject({ phase: "stopping", busy: true, canStop: false, run: running });
  });

  it("exposes stop only for a desktop-controlled run", () => {
    const desktop = run("desktop", "running", "2026-08-28T00:00:01Z");
    const voice = run("voice", "running", "2026-08-28T00:00:01Z", "voice");

    expect(sessionRunPresentation(source([desktop]), "session-a").canStop).toBe(true);
    expect(sessionRunPresentation(source([voice]), "session-a").canStop).toBe(false);
  });

  it("treats missing run authority as recovering until calibration succeeds", () => {
    expect(sessionRunPresentation(source([], "calibrating"), "session-a"))
      .toMatchObject({ phase: "recovering", busy: true, canStop: false });
    expect(sessionRunPresentation(source([], "degraded"), "session-a"))
      .toMatchObject({ phase: "recovering", busy: true, canStop: false });
    expect(sessionRunPresentation(source([], "ready"), "session-a"))
      .toMatchObject({ phase: "idle", busy: false, canStop: false });
  });

  it("hides the whole action bar only for the assistant message owned by the active run", () => {
    const activeMessage = {
      id: "assistant-active",
      role: "assistant" as const,
      runId: "run-active",
      createdAt: "2026-08-28T00:00:00Z",
      blocks: [],
      status: "complete" as const,
    };
    const historicalMessage = { ...activeMessage, id: "assistant-old", runId: "run-old" };

    expect(shouldShowMessageActionBar(activeMessage, "run-active")).toBe(false);
    expect(shouldShowMessageActionBar(historicalMessage, "run-active")).toBe(true);
    expect(shouldShowMessageActionBar({ ...historicalMessage, status: "streaming" }, "run-active"))
      .toBe(false);
  });
});
