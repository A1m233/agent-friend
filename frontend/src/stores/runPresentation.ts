import type { RunSnapshot } from "@/types/run";
import type { ChatMessage } from "@/types/chat";

export type RunCalibrationPhase = "calibrating" | "ready" | "degraded";

export interface RunPresentationSource {
  runSnapshots: Record<string, RunSnapshot>;
  runCalibrationPhase: RunCalibrationPhase;
  stopRequestsByRun: Record<string, unknown>;
}

export interface SessionRunPresentation {
  phase: "recovering" | "queued" | "running" | "stopping" | "idle";
  run: RunSnapshot | null;
  busy: boolean;
  canStop: boolean;
}

function compareActiveRuns(left: RunSnapshot, right: RunSnapshot): number {
  const leftRank = left.status === "running" ? 0 : 1;
  const rightRank = right.status === "running" ? 0 : 1;
  if (leftRank !== rightRank) return leftRank - rightRank;
  const created = left.created_at.localeCompare(right.created_at);
  return created !== 0 ? created : left.run_id.localeCompare(right.run_id);
}

export function sessionActiveRun(
  source: Pick<RunPresentationSource, "runSnapshots">,
  sessionId: string | null | undefined,
): RunSnapshot | null {
  if (!sessionId) return null;
  return Object.values(source.runSnapshots)
    .filter((run) => run.session_id === sessionId && run.status !== "terminal")
    .sort(compareActiveRuns)[0] ?? null;
}

export function sessionRunPresentation(
  source: RunPresentationSource,
  sessionId: string | null | undefined,
): SessionRunPresentation {
  const run = sessionActiveRun(source, sessionId);
  if (run) {
    const stopping = Boolean(source.stopRequestsByRun[run.run_id]);
    return {
      phase: stopping ? "stopping" : run.status === "queued" ? "queued" : "running",
      run,
      busy: true,
      canStop: run.control_scope === "desktop_text" && !stopping,
    };
  }
  if (source.runCalibrationPhase !== "ready") {
    return {
      phase: "recovering",
      run: null,
      busy: true,
      canStop: false,
    };
  }
  return { phase: "idle", run: null, busy: false, canStop: false };
}

export function isSessionBusy(
  source: RunPresentationSource,
  sessionId: string | null | undefined,
): boolean {
  return sessionRunPresentation(source, sessionId).busy;
}

export function isActiveRunMessage(
  message: ChatMessage,
  activeRunId: string | null | undefined,
): boolean {
  return Boolean(
    activeRunId && message.role === "assistant" && message.runId === activeRunId,
  );
}

export function activeRunActivityMessageId(
  messages: readonly ChatMessage[],
  activeRunId: string | null | undefined,
): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message && isActiveRunMessage(message, activeRunId)) return message.id;
  }
  return null;
}

export function shouldShowMessageActionBar(
  message: ChatMessage,
  activeRunId: string | null | undefined,
  editing = false,
): boolean {
  return message.status !== "streaming"
    && !editing
    && !isActiveRunMessage(message, activeRunId);
}
