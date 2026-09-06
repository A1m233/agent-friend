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
  /** Process-local Bridge runtime that owns this run. */
  runtime_instance_id?: string | null;
  /** True only after the submitted input is durable; null/absent is legacy or not applicable. */
  input_accepted?: boolean | null;
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

export interface RuntimeIdentitySnapshot {
  bridgeInstanceId: string;
  supervisorGeneration: number;
  startedAt: string;
  startReason: string;
}

export interface SessionRuntimeStateSnapshot {
  resident: true;
  modelHistoryGeneration: number;
  providerRequestCount: number;
  cacheEpochGeneration: number;
  cacheEpochReason: string | null;
}

export interface SessionRuntimeSnapshotResponse {
  runtime: RuntimeIdentitySnapshot;
  sessionRuntime: SessionRuntimeStateSnapshot | null;
  runs: RunSnapshot[];
  approvals: unknown[];
  commandProcesses: unknown[];
}

export interface RunEventJournalRecord {
  event_seq: number;
  event: import("./push").PushEvent;
}

export interface RunEventJournalResponse {
  runtime_instance_id: string;
  run_id: string;
  session_id: string;
  first_seq: number;
  next_seq: number;
  truncated: boolean;
  gap_before_requested_seq: boolean;
  events: RunEventJournalRecord[];
}
