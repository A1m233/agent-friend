import { describe, expect, it } from "vitest";
import type { Todo, TodoReminder } from "@/services/api/todos";
import { formatReminderTime, reminderTime } from "./reminder-time";

const now = Date.parse("2026-10-25T07:00:00Z");
const at = (minutes: number) => new Date(now + minutes * 60_000).toISOString();
function reminder(overrides: Partial<TodoReminder> = {}): TodoReminder {
  return { id: "r", todoId: "t", schedule: {}, status: "active", revision: 1,
    nextFireAtUtc: null, onceAtUtc: null, lastOccurrenceAtUtc: null, ...overrides };
}
function todo(reminders: TodoReminder[], status: Todo["status"] = "open"): Todo {
  return { id: "t", title: "任务", description: "", status, revision: 1,
    createdAt: at(-100), updatedAt: at(-1), lastOperation: null, reminders };
}

describe("reminder time selection", () => {
  it("prefers the earliest future over a nearer past occurrence, regardless of array order", () => {
    const value = todo([
      reminder({ nextFireAtUtc: at(200), lastOccurrenceAtUtc: at(-1) }),
      reminder({ nextFireAtUtc: at(90) }),
      reminder({ status: "disabled", onceAtUtc: at(1) }),
    ]);
    expect(reminderTime(value, now)?.at).toBe(at(90));
  });
  it("keeps the latest past time after exhaustion, or before the scheduler processes it", () => {
    const value = todo([
      reminder({ status: "exhausted", lastOccurrenceAtUtc: at(-50) }),
      reminder({ status: "exhausted", onceAtUtc: at(-20) }),
      reminder({ nextFireAtUtc: at(-5) }),
    ]);
    expect(reminderTime(value, now)?.at).toBe(at(-5));
    expect(reminderTime(todo(value.reminders.slice(0, 2)), now)?.at).toBe(at(-20));
  });
  it.each(["completed", "cancelled"] as const)("mutes %s tasks", (status) => {
    expect(reminderTime(todo([reminder({ onceAtUtc: at(-10) })], status), now)?.color).toBe("text-muted");
  });
  it("retains a disabled one-off plan without presenting it as enabled", () => {
    expect(reminderTime(todo([reminder({ status: "disabled", onceAtUtc: at(10) })]), now))
      .toMatchObject({ at: at(10), color: "text-muted", disabled: true });
  });
  it("ignores missing/invalid times without inventing a time from an update or creation", () => {
    expect(reminderTime(todo([]), now)).toBeNull();
    expect(reminderTime(todo([reminder({ nextFireAtUtc: "bad" })]), now)).toBeNull();
  });
  it("compares timezone offsets as instants", () => {
    expect(reminderTime(todo([reminder({ nextFireAtUtc: "2026-10-25T15:25:00+08:00" })]), now))
      .toMatchObject({ relative: "还有 25 分钟", color: "text-warning" });
    expect(formatReminderTime(at(25))).not.toContain("Invalid Date");
  });
});

it.each([
  [-12, "已过期 12 分钟", "text-danger"],
  [-0.1, "已过期 不到 1 分钟", "text-danger"],
  [0, "现在到期", "text-warning"],
  [0.1, "还有 不到 1 分钟", "text-warning"],
  [1, "还有 1 分钟", "text-warning"],
  [59.99, "还有 59 分钟", "text-warning"],
  [60, "还有 1 小时", "text-success"],
  [200, "还有 3 小时 20 分钟", "text-success"],
  [1440, "还有 1 天", "text-success"],
  [3060, "还有 2 天 3 小时", "text-success"],
] as const)("formats %s minutes with a matching urgency", (minutes, relative, color) => {
  expect(reminderTime(todo([reminder({ nextFireAtUtc: at(minutes) })]), now))
    .toMatchObject({ relative, color });
});
