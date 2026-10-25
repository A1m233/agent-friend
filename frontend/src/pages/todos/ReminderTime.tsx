import type { Todo } from "@/services/api/todos";
import { formatReminderTime, reminderTime } from "./reminder-time";

export function ReminderTime({ todo, now }: { todo: Todo; now: number }) {
  const timing = reminderTime(todo, now);
  if (!timing) return <span className="text-xs text-muted">{
    !todo.reminders.length ? "未设置提醒"
      : todo.reminders.every((item) => item.status === "disabled") ? "提醒已停用" : "暂无提醒时间"
  }</span>;
  return <span className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs ${timing.color}`}
    aria-label="提醒时间" title="原定提醒时间；时间已过不代表通知已送达">
    <span>{timing.disabled ? "提醒已停用：" : "提醒："}<time dateTime={timing.at}>{formatReminderTime(timing.at)}</time></span>
    {todo.status !== "completed" && <span>{timing.relative}</span>}
  </span>;
}
