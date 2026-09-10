import { StatusDot } from "@/components/ui";
import type { ChatMessage, CompactionBlock } from "@/types/chat";
import { ModelResponseStatus } from "../ModelResponseStatus";
import type { MessagePrimaryActivity } from "./primaryActivity";
import { RunNotice } from "./RunNotice";

export function CompactionActivity({
  block,
  isPrimaryActivity,
  legacyActivity,
}: {
  block: CompactionBlock;
  isPrimaryActivity: boolean;
  legacyActivity: boolean;
}) {
  const running = block.status === "running";
  const live = running && (isPrimaryActivity || legacyActivity);
  return (
    <p
      className="flex items-center gap-2 px-1 text-xs text-muted"
      role={live ? "status" : undefined}
      data-primary-activity={isPrimaryActivity ? "compaction" : undefined}
    >
      <StatusDot tone="muted" pulse={live} />
      {running
        ? live ? "正在整理较长对话…" : "较长对话整理"
        : "已整理较长对话"}
    </p>
  );
}

export function MessagePrimaryActivityStatus({
  activity,
}: {
  activity: MessagePrimaryActivity | null;
}) {
  if (activity?.kind === "model") {
    return (
      <ModelResponseStatus
        status={activity.status}
        primaryActivityKind="model"
      />
    );
  }
  if (activity?.kind === "run") {
    return (
      <ModelResponseStatus
        label={activity.label}
        primaryActivityKind="run"
      />
    );
  }
  return null;
}

/** Current activity is transient, outside disclosure; terminal facts remove it. */
export function MessageActivityTail({ message, activity }: {
  message: ChatMessage;
  activity?: MessagePrimaryActivity | null;
}) {
  if (message.runOutcome || message.status === "error" || message.status === "interrupted") {
    const outcome = message.runOutcome;
    const label = outcome === "failed" || message.status === "error"
      ? message.error ? "" : "执行失败"
      : outcome === "expired" ? "任务等待时间过长，已过期"
      : outcome === "skipped" ? "本轮任务未执行"
      : outcome === "rejected" ? "本轮任务未被接纳" : "";
    return label ? (
      <RunNotice message={label} outcome />
    ) : null;
  }
  if (activity === undefined) {
    return message.status === "streaming" ? (
      <div data-message-activity-tail><ModelResponseStatus status={message.runStatus} /></div>
    ) : null;
  }
  if (activity?.kind !== "model" && activity?.kind !== "run") return null;
  return (
    <div data-message-activity-tail><MessagePrimaryActivityStatus activity={activity} /></div>
  );
}
