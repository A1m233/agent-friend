import { describe, it, expect } from "vitest";
import { buildSchedule, buildScheduleChange, readScheduleForm } from "./schedule";

const form = {
  cadence: "daily",
  time: "09:00",
  onceAt: "2027-01-01T10:00",
  interval: "2",
  weekdays: "1,7",
  monthDays: "1,31",
  timezone: "Asia/Shanghai",
};
describe("scheduled request calendar input", () => {
  it("uses the selected timezone date and ISO weekdays", () => {
    expect(
      buildSchedule(
        { ...form, cadence: "weekdays" },
        new Date("2026-01-01T20:00:00Z"),
      ),
    ).toMatchObject({
      weekdays: [1, 2, 3, 4, 5],
      starts_on: "2026-01-02",
      timezone: "Asia/Shanghai",
    });
    expect(buildSchedule({ ...form, cadence: "weekly" })).toMatchObject({
      weekdays: [1, 7],
    });
    expect(
      buildSchedule({ ...form, cadence: "weekly", weekdays: "" }),
    ).toMatchObject({ weekdays: [] });
  });
  it("keeps existing schedules intact and separates elapsed hours from calendar time", () => {
    expect(buildSchedule({ ...form, cadence: "interval" })).toEqual({
      kind: "fixed_interval",
      interval: 2,
      unit: "hour",
    });
    expect(buildSchedule({ ...form, cadence: "once" })).toEqual({
      kind: "once",
      local_datetime: form.onceAt,
      timezone: "Asia/Shanghai",
    });
  });
});

describe("editing a saved schedule", () => {
  const weekly = {
    kind: "recurring", frequency: "weekly", interval: 1,
    local_time: "16:37:25", timezone: "Asia/Shanghai", starts_on: "2030-01-10",
    weekdays: [7, 1, 3], until_local: "2031-01-01T00:00:00", max_occurrences: 12, fold: 1,
  };
  it("prefills the actual frequency, local time and weekday selection", () => {
    expect(readScheduleForm(weekly)).toMatchObject({ cadence: "weekly", time: "16:37", weekdays: "1,3,7" });
    expect(readScheduleForm({ ...weekly, weekdays: [1, 2, 3, 4, 5] }).cadence).toBe("weekdays");
    expect(readScheduleForm({ ...weekly, frequency: "daily" }).cadence).toBe("daily");
    expect(readScheduleForm({ kind: "once", timezone: "Asia/Shanghai", local_datetime: "2030-01-12T00:07:30" }))
      .toMatchObject({ cadence: "once", onceAt: "2030-01-12T00:07" });
  });
  it("omits unchanged schedules, preserving seconds and all hidden constraints", () => {
    expect(buildScheduleChange(readScheduleForm(weekly), weekly)).toBeUndefined();
    const once = { kind: "once", timezone: "America/New_York", local_datetime: "2030-11-03T01:30:45", fold: 1 };
    expect(buildScheduleChange(readScheduleForm(once), once)).toBeUndefined();
    const unsupported = { kind: "fixed_interval", interval_seconds: 7200, anchor_at_utc: "2030-01-01T00:00:00Z" };
    expect(buildScheduleChange(readScheduleForm(unsupported), unsupported)).toBeUndefined();
  });
  it("changes the visible time without moving the original start date or dropping limits", () => {
    const result = buildScheduleChange({ ...readScheduleForm(weekly), time: "20:15" }, weekly);
    expect(result?.kind).toBe("calendar");
    expect(result).toMatchObject({ local_time: "20:15", starts_on: "2030-01-10", until_local: weekly.until_local, max_occurrences: 12, fold: 1 });
    expect(result?.weekdays).toEqual([1, 3, 7]);
  });
  it("preserves hidden seconds when only changing days and omits a reverted edit", () => {
    const initial = readScheduleForm(weekly);
    expect(buildScheduleChange({ ...initial, weekdays: "2,4" }, weekly)?.local_time).toBe("16:37:25");
    expect(buildScheduleChange({ ...initial, cadence: "daily", weekdays: "2,4", onceAt: "other" }, { ...weekly, frequency: "daily" })).toBeUndefined();
    const once = { kind: "once", timezone: "Asia/Shanghai", local_datetime: "2030-01-01T12:30:25", fold: 0 };
    expect(buildScheduleChange({ ...readScheduleForm(once), onceAt: "2030-01-02T12:30" }, once)?.local_datetime).toBe("2030-01-02T12:30:25");
  });
});
