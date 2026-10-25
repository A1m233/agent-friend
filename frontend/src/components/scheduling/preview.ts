import { http } from "@/services/http";

/** The existing preview endpoint uses the same scheduling primitives as Todo reminders. */
export async function previewSchedule(schedule: Record<string, unknown>): Promise<string | null> {
  const result = await http.post<unknown, { next_fire_at_utc: string | null }>(
    "/v1/scheduled-tasks/preview",
    { schedule: schedule.kind === "recurring" ? { ...schedule, kind: "calendar" } : schedule },
  );
  return result.next_fire_at_utc;
}
