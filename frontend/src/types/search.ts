export interface SearchTextSegment {
  text: string;
  matched: boolean;
}

export interface SessionTitleSearchResult {
  result_id: string;
  kind: "title";
  session_id: string;
  session_title: string;
  archived: boolean;
  updated_at: string;
  segments: SearchTextSegment[];
}

export interface SessionMessageSearchResult {
  result_id: string;
  kind: "message";
  session_id: string;
  session_title: string;
  archived: boolean;
  role: "user" | "assistant";
  occurred_at: string;
  anchor_event_uuid: string;
  matched_field: "content" | "attachment_name";
  segments: SearchTextSegment[];
}

export type SessionSearchResult = SessionTitleSearchResult | SessionMessageSearchResult;

export interface SearchNavigationTarget {
  token: number;
  sessionId: string;
  anchorEventUuid: string;
  query: string;
  /** Keep the anchor pending while older turn pages are being loaded. */
  loading?: boolean;
}

export interface SessionSearchResponse {
  query: string;
  title_total: number;
  message_total: number;
  title_results: SessionTitleSearchResult[];
  message_results: SessionMessageSearchResult[];
}
