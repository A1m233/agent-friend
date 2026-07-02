/**
 * 会话 meta REST（006 design §4.11）。上层（stores）只调这里，不直接碰 axios。
 * 错误已在 http 拦截器统一兜底为 FriendlyError。
 */

import { http } from "../http";
import type { SessionDetail, SessionSummary } from "@/types/meta";

interface ListSessionsOptions {
  archived?: boolean;
  q?: string;
}

interface DeleteArchivedSessionsOptions {
  q?: string;
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
  /** 列出历史会话（GET /v1/sessions）。 */
  list(options?: ListSessionsOptions): Promise<SessionSummary[]> {
    return http.get<unknown, SessionSummary[]>(`/v1/sessions${sessionQuery(options)}`);
  },

  /** 单个会话详情含事件流（GET /v1/sessions/{id}）。 */
  get(sessionId: string): Promise<SessionDetail> {
    return http.get<unknown, SessionDetail>(`/v1/sessions/${sessionId}`);
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
