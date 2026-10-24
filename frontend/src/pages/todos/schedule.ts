export interface ReminderForm {
  kind: string;
  timezone: string;
  onceAt: string;
  frequency: string;
  interval: string;
  time: string;
  startsOn: string;
  weekdays: string;
  monthDays: string;
  until: string;
  count: string;
  fold: string;
}

export function readReminderForm(schedule?: Record<string, unknown>): ReminderForm {
  return {
    kind: String(schedule?.kind ?? "once"),
    timezone: String(schedule?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone),
    onceAt: String(schedule?.local_datetime ?? ""),
    frequency: String(schedule?.frequency ?? "daily"), interval: String(schedule?.interval ?? 1),
    time: String(schedule?.local_time ?? "09:00:00"), startsOn: String(schedule?.starts_on ?? ""),
    weekdays: Array.isArray(schedule?.weekdays) ? schedule.weekdays.join(",") : "1",
    monthDays: Array.isArray(schedule?.month_days) ? schedule.month_days.join(",") : "1",
    until: String(schedule?.until_local ?? ""), count: String(schedule?.max_occurrences ?? ""),
    fold: String(schedule?.fold ?? 0),
  };
}

export function buildReminderSchedule(form: ReminderForm): Record<string, unknown> {
  const base = { kind: form.kind, timezone: form.timezone.trim(), fold: Number(form.fold) };
  if (!base.timezone) throw new Error("请填写提醒时区。");
  if (form.kind === "once") {
    if (!form.onceAt) throw new Error("请选择提醒时间。");
    return { ...base, local_datetime: form.onceAt };
  }
  const interval = Number(form.interval);
  if (!Number.isInteger(interval) || interval < 1) throw new Error("重复间隔必须为正整数。");
  if (!form.startsOn || !form.time) throw new Error("请填写开始日期和提醒时间。");
  const numbers = (text: string, max: number) => {
    const values = text.split(/[,，]/).map((item) => Number(item.trim()));
    if (!values.length || values.some((n) => !Number.isInteger(n) || n < 1 || n > max))
      throw new Error(`请输入 1 到 ${max} 之间的日期，用逗号分隔。`);
    return [...new Set(values)].sort((a, b) => a - b);
  };
  const count = form.count ? Number(form.count) : null;
  if (count !== null && (!Number.isInteger(count) || count < 1)) throw new Error("提醒次数必须为正整数。");
  return {
    ...base, kind: "recurring", frequency: form.frequency, interval,
    local_time: form.time, starts_on: form.startsOn,
    weekdays: form.frequency === "weekly" ? numbers(form.weekdays, 7) : [],
    month_days: form.frequency === "monthly" ? numbers(form.monthDays, 31) : [],
    until_local: form.until || null, max_occurrences: count,
  };
}

export function describeReminder(schedule: Record<string, unknown>): string {
  if (schedule.kind === "once") return `${String(schedule.local_datetime).replace("T", " ")} · ${schedule.timezone}`;
  const unit = { daily: "天", weekly: "周", monthly: "月" }[String(schedule.frequency)] ?? "周期";
  const days = schedule.frequency === "weekly" && Array.isArray(schedule.weekdays)
    ? ` · 周${schedule.weekdays.map((n) => ["", "一", "二", "三", "四", "五", "六", "日"][Number(n)]).join("、")}`
    : schedule.frequency === "monthly" && Array.isArray(schedule.month_days) ? ` · ${schedule.month_days.join("、")} 日` : "";
  return `每 ${schedule.interval} ${unit}${days} · ${schedule.local_time} · ${schedule.timezone}`;
}
