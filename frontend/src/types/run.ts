export type RunStatus = "queued" | "running" | "terminal";
export type TurnStatus = "in_progress" | "completed" | "interrupted" | "failed";
export type RunOutcome =
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped"
  | "expired"
  | "rejected";

export interface RunSnapshot {
  run_id: string;
  session_id: string;
  status: RunStatus;
  outcome: RunOutcome | null;
  reason_code: string;
  channel: "text" | "voice" | null;
  origin: string | null;
  control_scope: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  version: number;
  turn_status?: TurnStatus | null;
  total_tool_calls?: number;
  tool_call_items?: number;
  model_calls?: number;
  model_continuations?: number;
  budget_dimension?: string | null;
}

export interface RunListResponse {
  runs: RunSnapshot[];
}

export interface CancelRunResponse {
  result: "accepted" | "already_requested" | "already_terminal";
  run: RunSnapshot;
}
