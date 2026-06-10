/**
 * 会话 meta REST（006 design §4.11）。上层（stores）只调这里，不直接碰 axios。
 * 错误已在 http 拦截器统一兜底为 FriendlyError。
 */

import { http } from "../http";
import type { SessionDetail, SessionSummary } from "@/types/meta";

export const sessionsApi = {
  /** 列出历史会话（GET /v1/sessions）。 */
  list(): Promise<SessionSummary[]> {
    return http.get<unknown, SessionSummary[]>("/v1/sessions");
  },

  /** 单个会话详情含事件流（GET /v1/sessions/{id}）。 */
  get(sessionId: string): Promise<SessionDetail> {
    return http.get<unknown, SessionDetail>(`/v1/sessions/${sessionId}`);
  },
};
