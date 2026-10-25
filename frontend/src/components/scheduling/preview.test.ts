import { expect, it, vi } from "vitest";
import { previewSchedule } from "./preview";
const post = vi.hoisted(() => vi.fn());
vi.mock("@/services/http", () => ({ http: { post } }));
it("adapts reminder recurrence to the existing preview without dropping constraints", async () => {
  const schedule = { kind: "recurring", frequency: "monthly", interval: 2, month_days: [1, 15],
    starts_on: "2030-01-01", until_local: "2031-01-01T01:30:45", max_occurrences: 12,
    local_time: "01:30:45", timezone: "America/New_York", fold: 1 };
  post.mockResolvedValue({ next_fire_at_utc: "2030-01-01T06:30:45Z" });
  expect(await previewSchedule(schedule)).toBe("2030-01-01T06:30:45Z");
  expect(post).toHaveBeenCalledWith("/v1/scheduled-tasks/preview", { schedule: { ...schedule, kind: "calendar" } });
  expect(schedule.kind).toBe("recurring");
});
