import type { ChatMessage } from "@/types/chat";
import type { RunSnapshot } from "@/types/run";

export function isRunAwaitingStart(message: ChatMessage, run: RunSnapshot | undefined): boolean {
  if (run) return run.status === "queued";
  // A fresh optimistic placeholder is not a historical record with missing timing data.
  return message.status === "streaming" && message.blocks.length === 0
    && !message.runOutcome && !message.runStartedAt && !message.runFinishedAt;
}

export function elapsedRunSeconds(message: ChatMessage, run: RunSnapshot | undefined, now: number): number | null {
  const startedAt = run?.started_at ?? message.runStartedAt;
  const finishedAt = run?.finished_at ?? message.runFinishedAt;
  if (!startedAt || (!finishedAt && run?.status !== "running")) return null;
  const start = Date.parse(startedAt);
  const end = finishedAt ? Date.parse(finishedAt) : now;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return message.priorExecutionSeconds === null ? null
    : Math.floor((message.priorExecutionSeconds ?? 0) + (end - start) / 1000);
}

export function formatRunDuration(seconds: number | null, state: "running" | "stopped" | "finished" = "finished"): string {
  if (seconds === null) {
    return state === "stopped" ? "已停止，用时未知"
      : state === "running" ? "进行中，用时未知" : "用时未知";
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const remainder = seconds % 60;
  const duration = `${hours ? `${hours}小时 ` : ""}${minutes || hours ? `${minutes}分 ` : ""}${remainder}秒`;
  return state === "stopped" ? `${duration} 后被停止`
    : state === "running" ? `已进行 ${duration}` : `用时 ${duration}`;
}
