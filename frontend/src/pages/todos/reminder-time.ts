import { formatTimestamp } from "@/components/scheduling/presentation";
import type { Todo, TodoReminder } from "@/services/api/todos";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

interface Candidate { at: string; timestamp: number; reminder: TodoReminder }

/** Prefer the next enabled reminder; otherwise keep the latest past scheduled time. */
export function reminderTime(todo: Todo, now: number) {
  const candidates: Candidate[] = todo.reminders.flatMap((reminder) => {
    const dates = [reminder.onceAtUtc, reminder.lastOccurrenceAtUtc];
    if (reminder.status === "active") dates.push(reminder.nextFireAtUtc);
    return dates.flatMap((at) => {
      const timestamp = at ? Date.parse(at) : NaN;
      return at && Number.isFinite(timestamp) ? [{ at, timestamp, reminder }] : [];
    });
  });
  const future = candidates.filter((item) => item.timestamp > now
    && item.reminder.status === "active" && todo.status === "open")
    .sort((a, b) => a.timestamp - b.timestamp);
  const past = candidates.filter((item) => item.timestamp <= now)
    .sort((a, b) => b.timestamp - a.timestamp);
  // Closed/disabled one-off plans can still show their original time in muted text.
  const selected = future[0] ?? past[0] ?? candidates.sort((a, b) => a.timestamp - b.timestamp)[0];
  if (!selected) return null;
  const difference = selected.timestamp - now;
  const muted = todo.status !== "open" || selected.reminder.status === "disabled";
  const color = muted ? "text-muted" : difference < 0 ? "text-danger"
    : difference < HOUR ? "text-warning" : "text-success";
  return {
    at: selected.at,
    color,
    disabled: selected.reminder.status === "disabled",
    relative: difference === 0 ? "现在到期" : `${difference < 0 ? "已过期" : "还有"} ${duration(Math.abs(difference))}`,
  };
}

function duration(milliseconds: number): string {
  if (milliseconds < MINUTE) return "不到 1 分钟";
  if (milliseconds < HOUR) return `${Math.floor(milliseconds / MINUTE)} 分钟`;
  if (milliseconds < DAY) {
    const minutes = Math.floor(milliseconds % HOUR / MINUTE);
    return `${Math.floor(milliseconds / HOUR)} 小时${minutes ? ` ${minutes} 分钟` : ""}`;
  }
  const hours = Math.floor(milliseconds % DAY / HOUR);
  return `${Math.floor(milliseconds / DAY)} 天${hours ? ` ${hours} 小时` : ""}`;
}

export function formatReminderTime(at: string): string {
  return formatTimestamp(at, false);
}
