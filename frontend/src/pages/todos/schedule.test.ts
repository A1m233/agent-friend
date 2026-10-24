import { describe, expect, it } from "vitest";
import { buildReminderSchedule, readReminderForm } from "./schedule";

describe("Todo reminder schedule round trips", () => {
  it("preserves seconds, timezone and DST fold for one-time reminders", () => {
    const schedule = { kind: "once", local_datetime: "2030-11-03T01:30:45", timezone: "America/New_York", fold: 1 };
    expect(buildReminderSchedule(readReminderForm(schedule))).toEqual(schedule);
  });
  it.each(["daily", "weekly", "monthly"])("preserves %s constraints while editing", (frequency) => {
    const schedule = { kind: "recurring", frequency, interval: 3, local_time: "18:42:17", timezone: "Asia/Shanghai",
      starts_on: "2030-10-24", weekdays: frequency === "weekly" ? [1, 4, 7] : [],
      month_days: frequency === "monthly" ? [1, 15, 31] : [], until_local: "2031-10-24T19:20:10", max_occurrences: 12, fold: 1 };
    expect(buildReminderSchedule(readReminderForm(schedule))).toEqual(schedule);
  });
  it("rejects empty dates and invalid intervals/days/counts before sending", () => {
    expect(() => buildReminderSchedule(readReminderForm())).toThrow("时间");
    const form = { ...readReminderForm(), kind: "recurring", startsOn: "2030-10-24", frequency: "weekly", weekdays: "1,8" };
    expect(() => buildReminderSchedule(form)).toThrow("1 到 7");
    expect(() => buildReminderSchedule({ ...form, weekdays: "1", interval: "0" })).toThrow("间隔");
    expect(() => buildReminderSchedule({ ...form, weekdays: "1", count: "-1" })).toThrow("次数");
  });
});
