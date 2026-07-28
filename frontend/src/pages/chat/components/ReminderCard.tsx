import { CalendarClock, CheckCircle2, Circle, Repeat2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui";
import type { ReminderBlock } from "@/types/chat";

const STATUS_META: Record<string, { label: string; icon: typeof Circle }> = {
  open: { label: "待处理", icon: Circle },
  completed: { label: "已完成", icon: CheckCircle2 },
  cancelled: { label: "已取消", icon: XCircle },
};

function scheduledTime(block: ReminderBlock): string {
  const match = block.scheduledLocal.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/,
  );
  if (!match) return block.scheduledLocal || block.scheduledAtUtc;
  const [, year, month, day, hour, minute] = match;
  return `${year}-${month}-${day} ${hour}:${minute}`;
}

function recurrenceLabel(value: string): string {
  if (!value || value === "once") return "一次性提醒";
  return value
    .replace(/^every (\d+) day\(s\)$/, "每 $1 天")
    .replace(/^every (\d+) week\(s\) on (.+)$/, "每 $1 周（星期 $2）")
    .replace(/^every (\d+) month\(s\) on day (.+)$/, "每 $1 个月（$2 日）");
}

export function ReminderCard({ block }: { block: ReminderBlock }) {
  const status = STATUS_META[block.todoStatus] ?? {
    label: block.todoStatus || "状态未知",
    icon: Circle,
  };
  const StatusIcon = status.icon;

  return (
    <article
      className="w-full max-w-[32rem] rounded-xl border border-border bg-surface/70 p-4 shadow-sm"
      aria-label={`提醒：${block.title}`}
    >
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent/10 text-accent">
          <CalendarClock className="size-4" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-xs font-medium text-muted">Todo 提醒</p>
              <h3 className="mt-0.5 break-words text-sm font-semibold text-fg">
                {block.title || "未命名 Todo"}
              </h3>
            </div>
            <Badge variant="secondary">
              <StatusIcon className="size-3" aria-hidden="true" />
              {status.label}
            </Badge>
          </div>
          {block.description && (
            <p className="mt-2 whitespace-pre-wrap break-words text-sm text-muted">
              {block.description}
            </p>
          )}
          <dl className="mt-3 grid gap-1.5 text-xs text-muted">
            <div className="flex items-center gap-2">
              <CalendarClock className="size-3.5 shrink-0" aria-hidden="true" />
              <dt className="sr-only">计划时间</dt>
              <dd>{scheduledTime(block)} · {block.timezone}</dd>
            </div>
            <div className="flex items-center gap-2">
              <Repeat2 className="size-3.5 shrink-0" aria-hidden="true" />
              <dt className="sr-only">周期</dt>
              <dd>{recurrenceLabel(block.recurrence)}</dd>
            </div>
          </dl>
        </div>
      </div>
    </article>
  );
}
