import { http } from "../http";

export interface ScheduledAsset {
  asset_id: string;
  display_name: string;
  kind: "image" | "document";
  media_type: string;
  size_bytes: number;
  sha256: string;
}
export interface RequestOptions {
  attachments: ScheduledAsset[];
  model: { model_id: string; reasoning_preset?: string } | null;
  approval_mode: "per_request" | "session_auto" | null;
}
export interface ScheduledTask {
  id: string;
  name: string;
  instruction: string;
  target_session_id: string | null;
  schedule: Record<string, unknown>;
  request_options: RequestOptions;
  status: "active" | "paused" | "exhausted" | "deleted";
  revision: number;
  next_fire_at_utc: string | null;
  pause_reason: string;
  needs_target: boolean;
  skill_id: string | null;
}
export interface TaskRun {
  effective_options?: RequestOptions | null;
  id: string;
  state: string;
  outcome: string | null;
  reason_code: string;
  run_id: string | null;
  session_id: string | null;
  user_event_id: string | null;
  waiting_for_approval: boolean;
  scheduled_at_utc: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface TaskInput {
  name: string;
  instruction: string;
  target_session_id: string;
  schedule?: Record<string, unknown>;
  request_options: RequestOptions;
  draft_attachments: { draft_id: string; kind: string }[];
  skill_id?: string | null;
  clear_skill?: boolean;
}
export const scheduledTasksApi = {
  list: (offset = 0, limit = 100) =>
    http.get<unknown, { tasks: ScheduledTask[] }>(
      `/v1/scheduled-tasks?limit=${limit}&offset=${offset}`,
    ),
  get: (id: string) =>
    http.get<unknown, ScheduledTask>(
      `/v1/scheduled-tasks/${encodeURIComponent(id)}`,
    ),
  create: (input: TaskInput) =>
    http.post<unknown, ScheduledTask>("/v1/scheduled-tasks", input),
  update: (task: ScheduledTask, input: TaskInput) =>
    http.patch<unknown, ScheduledTask>(`/v1/scheduled-tasks/${task.id}`, {
      ...input,
      revision: task.revision,
    }),
  action: (task: ScheduledTask, action: "pause" | "resume") =>
    http.post<unknown, ScheduledTask>(
      `/v1/scheduled-tasks/${task.id}/${action}`,
      { revision: task.revision },
    ),
  remove: (task: ScheduledTask) =>
    http.delete<unknown, ScheduledTask>(
      `/v1/scheduled-tasks/${task.id}?revision=${task.revision}`,
    ),
  run: (id: string) =>
    http.post<unknown, TaskRun>(`/v1/scheduled-tasks/${id}/runs`),
  history: (id: string, offset = 0) =>
    http.get<unknown, { occurrences: TaskRun[] }>(
      `/v1/scheduled-tasks/${id}/occurrences?limit=50&offset=${offset}`,
    ),
};
