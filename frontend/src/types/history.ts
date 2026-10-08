import type { SessionEvent } from "./meta";

export type HistoryBody = { state: "inline" } | { state: "deferred"; ref: string; byte_length: number };
export interface HistoryRun {
  run_id: string;
  status?: string;
  started_at?: string;
  finished_at?: string;
  source_run_id?: string;
  root_run_id?: string;
  assistant_phase?: { mode: "native" | "legacy"; validation: "pending" | "valid" | "missing_final" | "conflict" };
  can_continue?: boolean;
  error?: string;
  error_truncated?: boolean;
  error_event_uuid?: string;
  error_code?: string;
  error_reason?: string;
  recovery_action?: string;
}
export interface HistoryEvent extends SessionEvent {
  body: HistoryBody;
  message_id: string;
  message_start?: boolean;
  message_end?: boolean;
  run?: HistoryRun;
  result_event?: HistoryEvent;
  summary?: Record<string, unknown>;
}
export interface HistoryPage {
  navigation_id?: string;
  session_id: string;
  title: string;
  persona: string;
  model: string;
  reasoningPreset?: string;
  revision?: number;
  approval_mode?: "per_request" | "session_auto";
  approval_mode_version?: number;
  recovery?: { sourceRunId: string; rootRunId: string } | null;
  history_epoch: string;
  content_version: string;
  history_items: HistoryEvent[];
  before_cursor: string | null;
  after_cursor: string | null;
  has_before: boolean;
  has_after: boolean;
  history_cursor?: string | null;
}
export interface HistoryContent {
  text: string;
  offset: number;
  next_offset: number | null;
  total_chars: number;
  complete: boolean;
  content_version: string;
}
export interface HistoryFragment {
  navigationId?: string;
  sessionId: string;
  messageId: string;
  version: string;
  epoch: string;
  event: HistoryEvent;
  first: boolean;
  last: boolean;
}
