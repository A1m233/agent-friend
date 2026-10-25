export interface ScheduleForm {
  cadence: string;
  time: string;
  onceAt: string;
  interval?: string;
  weekdays: string;
  monthDays?: string;
  timezone: string;
}

/** Decode the saved wall-clock values without applying the machine's UTC offset. */
export function readScheduleForm(
  schedule?: Record<string, unknown>,
  timezone = Intl.DateTimeFormat().resolvedOptions().timeZone,
): ScheduleForm {
  const weekdays = Array.isArray(schedule?.weekdays)
    ? [...schedule.weekdays].sort((a, b) => Number(a) - Number(b)).join(",")
    : "1";
  let cadence = schedule ? "existing" : "daily";
  if (schedule?.kind === "once") cadence = "once";
  if (schedule?.kind === "recurring" && Number(schedule.interval ?? 1) === 1) {
    if (schedule.frequency === "daily") cadence = "daily";
    if (schedule.frequency === "weekly") cadence = weekdays === "1,2,3,4,5" ? "weekdays" : "weekly";
  }
  return {
    cadence,
    time: typeof schedule?.local_time === "string" ? schedule.local_time.slice(0, 5) : "09:00",
    onceAt: typeof schedule?.local_datetime === "string" ? schedule.local_datetime.slice(0, 16) : "",
    weekdays,
    timezone: String(schedule?.timezone ?? timezone),
  };
}

/** Omit an unchanged schedule so editing other fields cannot reset its anchor or occurrence count. */
export function buildScheduleChange(
  form: ScheduleForm,
  original?: Record<string, unknown>,
  now = new Date(),
): Record<string, unknown> | undefined {
  const initial = readScheduleForm(original, form.timezone);
  if (original && form.cadence === initial.cadence && form.timezone === initial.timezone) {
    const unchanged = form.cadence === "once" ? form.onceAt === initial.onceAt
      : form.cadence === "existing" || (form.time === initial.time &&
        (form.cadence !== "weekly" || form.weekdays === initial.weekdays));
    if (unchanged) return undefined;
  }
  const next = buildSchedule(form, now);
  if (!next || !original) return next;
  if (original.kind === "recurring" && next.kind === "calendar") {
    for (const key of ["starts_on", "until_local", "max_occurrences", "fold"])
      if (key in original) next[key] = original[key];
    if (form.time === initial.time) next.local_time = original.local_time;
  }
  if (original.kind === "once" && next.kind === "once") {
    if ("fold" in original) next.fold = original.fold;
    if (form.onceAt.slice(11) === initial.onceAt.slice(11))
      next.local_datetime = `${form.onceAt.slice(0, 10)}T${String(original.local_datetime).slice(11)}`;
  }
  return next;
}

export function buildSchedule(
  form: ScheduleForm,
  now = new Date(),
): Record<string, unknown> | undefined {
  const { cadence, time, onceAt, interval, weekdays, monthDays, timezone } =
    form;
  if (cadence === "existing") return undefined;
  if (cadence === "once")
    return { kind: "once", timezone, local_datetime: onceAt };
  if (cadence === "interval")
    return { kind: "fixed_interval", interval: Number(interval), unit: "hour" };
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  return {
    kind: "calendar",
    frequency: cadence === "weekdays" ? "weekly" : cadence,
    interval: 1,
    local_time: time,
    timezone,
    starts_on: today,
    ...(cadence === "weekdays"
      ? { weekdays: [1, 2, 3, 4, 5] }
      : cadence === "weekly"
        ? { weekdays: weekdays.split(",").filter(Boolean).map(Number) }
        : cadence === "monthly"
          ? { month_days: (monthDays ?? "").split(",").filter(Boolean).map(Number) }
          : {}),
  };
}


export { describeSchedule } from "@/components/scheduling/presentation";
