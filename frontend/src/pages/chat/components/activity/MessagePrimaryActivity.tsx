import { StatusDot } from "@/components/ui";
import type { CompactionBlock } from "@/types/chat";
import { ModelResponseStatus } from "../ModelResponseStatus";
import type { MessagePrimaryActivity } from "./primaryActivity";

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
