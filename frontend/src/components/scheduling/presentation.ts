/** Shared presentation only; each domain owns its schedule lifecycle and serialization. */
export function formatTimestamp(value: string | null | undefined, seconds = true): string {
  if (!value) return "暂无";
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    ...(seconds ? { second: "2-digit" as const } : {}), hour12: false,
  });
}

export function describeSchedule(schedule: Record<string, unknown>): string {
  if (schedule.kind === "once") return `仅一次 · ${String(schedule.local_datetime).replace("T", " ")} · ${schedule.timezone}`;
  if (schedule.kind === "fixed_interval") return `每 ${Number(schedule.interval_seconds) / 3600} 小时`;
  const units: Record<string, string> = { daily: "天", weekly: "周", monthly: "月" };
  const days = Array.isArray(schedule.weekdays) ? schedule.weekdays.map(day => ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"][Number(day)]).join("、") : "";
  const dates = Array.isArray(schedule.month_days) ? schedule.month_days.join("、") : "";
  const detail = schedule.frequency === "weekly" ? days : schedule.frequency === "monthly" && dates ? `${dates} 日` : "";
  return `每 ${schedule.interval ?? 1} ${units[String(schedule.frequency)] ?? "周期"}${detail ? ` · ${detail}` : ""} · ${schedule.local_time} · ${schedule.timezone}`;
}
