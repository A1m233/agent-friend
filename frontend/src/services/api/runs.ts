import type {
  CancelRunResponse,
  RunEventJournalResponse,
  RunListResponse,
  RunSnapshot,
  SessionRuntimeSnapshotResponse,
} from "@/types/run";
import { http } from "../http";

export const runsApi = {
  list(includeRecentTerminal = true): Promise<RunListResponse> {
    return http.get<unknown, RunListResponse>(
      `/v1/runs?includeRecentTerminal=${String(includeRecentTerminal)}`,
    );
  },

  get(runId: string): Promise<RunSnapshot> {
    return http.get<unknown, RunSnapshot>(`/v1/runs/${encodeURIComponent(runId)}`);
  },

  events(runId: string, afterSeq = 0): Promise<RunEventJournalResponse> {
    return http.get<unknown, RunEventJournalResponse>(
      `/v1/runs/${encodeURIComponent(runId)}/events?afterSeq=${afterSeq}`,
    );
  },

  runtimeSnapshot(sessionId: string): Promise<SessionRuntimeSnapshotResponse> {
    return http.get<unknown, SessionRuntimeSnapshotResponse>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/runtime-snapshot`,
    );
  },

  cancel(sessionId: string, runId: string, expectedRuntimeInstanceId?: string): Promise<CancelRunResponse> {
    const query = expectedRuntimeInstanceId
      ? `?expectedRuntimeInstanceId=${encodeURIComponent(expectedRuntimeInstanceId)}`
      : "";
    return http.post<unknown, CancelRunResponse>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel${query}`,
    );
  },
};
