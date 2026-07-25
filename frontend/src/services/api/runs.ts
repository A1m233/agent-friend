import type { CancelRunResponse, RunListResponse, RunSnapshot } from "@/types/run";
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

  cancel(sessionId: string, runId: string): Promise<CancelRunResponse> {
    return http.post<unknown, CancelRunResponse>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`,
    );
  },
};
