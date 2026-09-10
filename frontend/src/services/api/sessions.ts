/**
 * 会话 meta REST（006 design §4.11）。上层（stores）只调这里，不直接碰 axios。
 * 错误已在 http 拦截器统一兜底为 FriendlyError。
 */

import { http } from "../http";
import type { SessionDetail, SessionSummary } from "@/types/meta";
import type { SessionSearchResponse } from "@/types/search";
import type { ModelRuntimeConfig } from "@/lib/model-config";

interface ListSessionsOptions {
  archived?: boolean;
  q?: string;
}

interface DeleteArchivedSessionsOptions {
  q?: string;
}

interface SearchSessionsOptions {
  messageLimit?: number;
  titleLimit?: number;
}

export type SessionApprovalMode = "per_request" | "session_auto";

export interface SessionApprovalModeState {
  session_id: string;
  approval_mode: SessionApprovalMode;
  approval_mode_version: number;
}

export interface EnableSessionAutoForApprovalInput {
  requestId: string;
  sessionId: string;
  runId: string;
  requestVersion: number;
  modeVersion: number;
}

function sessionQuery(options?: ListSessionsOptions | DeleteArchivedSessionsOptions): string {
  const params = new URLSearchParams();
  if (hasArchivedOption(options)) {
    params.set("archived", String(options.archived));
  }
  const query = options?.q?.trim();
  if (query) params.set("q", query);
  const raw = params.toString();
  return raw ? `?${raw}` : "";
}

function hasArchivedOption(
  options: ListSessionsOptions | DeleteArchivedSessionsOptions | undefined,
): options is ListSessionsOptions & { archived: boolean } {
  return options !== undefined && "archived" in options && options.archived !== undefined;
}

export const sessionsApi = {
  create(): Promise<{
    session_id: string;
    persona: string;
    model: string;
    reasoningPreset: string;
    channel: string;
  }> {
    return http.post<unknown, {
      session_id: string;
      persona: string;
      model: string;
      reasoningPreset: string;
      channel: string;
    }>(
      "/v1/sessions",
      { channel: "text" },
    );
  },
  /** 列出历史会话（GET /v1/sessions）。 */
  list(options?: ListSessionsOptions): Promise<SessionSummary[]> {
    return http.get<unknown, SessionSummary[]>(`/v1/sessions${sessionQuery(options)}`);
  },

  search(query: string, options?: SearchSessionsOptions): Promise<SessionSearchResponse> {
    const params = new URLSearchParams({ q: query.trim() });
    if (options?.messageLimit !== undefined) {
      params.set("message_limit", String(options.messageLimit));
    }
    if (options?.titleLimit !== undefined) {
      params.set("title_limit", String(options.titleLimit));
    }
    return http.get<unknown, SessionSearchResponse>(
      `/v1/sessions/search?${params.toString()}`,
    );
  },

  /** 单个会话详情只取当前有效事件，避免重复传输原始事件流。 */
  get(sessionId: string): Promise<SessionDetail> {
    return http.get<unknown, SessionDetail>(
      `/v1/sessions/${encodeURIComponent(sessionId)}?view=active&turn_limit=30`,
    );
  },

  getFull(sessionId: string): Promise<SessionDetail> {
    return http.get<unknown, SessionDetail>(`/v1/sessions/${encodeURIComponent(sessionId)}?view=active`);
  },

  getOlder(sessionId: string, before: string, epoch: string): Promise<SessionDetail> {
    const query = new URLSearchParams({ view: "active", turn_limit: "30", before, history_epoch: epoch });
    return http.get<unknown, SessionDetail>(`/v1/sessions/${encodeURIComponent(sessionId)}?${query}`);
  },

  getApprovalMode(sessionId: string): Promise<SessionApprovalModeState> {
    return http.get<unknown, SessionApprovalModeState>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/approval-mode`,
    );
  },

  updateApprovalMode(
    sessionId: string,
    mode: SessionApprovalMode,
    expectedVersion: number,
    riskAcknowledged: boolean,
  ): Promise<SessionApprovalModeState> {
    return http.put<unknown, SessionApprovalModeState>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/approval-mode`,
      {
        mode,
        expected_version: expectedVersion,
        risk_acknowledged: riskAcknowledged,
      },
    );
  },

  enableSessionAutoForApproval(
    input: EnableSessionAutoForApprovalInput,
  ): Promise<SessionApprovalModeState & { accepted: true }> {
    return http.post<unknown, SessionApprovalModeState & { accepted: true }>(
      `/v1/approvals/${encodeURIComponent(input.requestId)}/enable-session-auto`,
      {
        session_id: input.sessionId,
        run_id: input.runId,
        expected_request_version: input.requestVersion,
        expected_mode_version: input.modeVersion,
        risk_acknowledged: true,
      },
    );
  },

  switchModel(sessionId: string, model: string): Promise<{ session_id: string; model: string }> {
    return http.post<unknown, { session_id: string; model: string }>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/model`,
      { model },
    );
  },

  updateRuntimeConfig(
    sessionId: string,
    config: ModelRuntimeConfig,
  ): Promise<{ sessionId: string; runtimeConfig: ModelRuntimeConfig }> {
    return http.patch<unknown, { sessionId: string; runtimeConfig: ModelRuntimeConfig }>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/runtime-config`,
      config,
    );
  },

  rename(sessionId: string, title: string): Promise<SessionSummary> {
    return http.patch<unknown, SessionSummary>(`/v1/sessions/${sessionId}`, { title });
  },

  archive(sessionId: string): Promise<SessionSummary> {
    return http.patch<unknown, SessionSummary>(`/v1/sessions/${sessionId}`, { archived: true });
  },

  unarchive(sessionId: string): Promise<SessionSummary> {
    return http.patch<unknown, SessionSummary>(`/v1/sessions/${sessionId}`, { archived: false });
  },

  delete(sessionId: string): Promise<{ session_id: string; deleted: true }> {
    return http.delete<unknown, { session_id: string; deleted: true }>(`/v1/sessions/${sessionId}`);
  },

  deleteArchived(
    options?: DeleteArchivedSessionsOptions,
  ): Promise<{ deleted: number; session_ids: string[] }> {
    return http.delete<unknown, { deleted: number; session_ids: string[] }>(
      `/v1/sessions/archived${sessionQuery(options)}`,
    );
  },
};
