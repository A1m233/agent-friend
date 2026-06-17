/**
 * IM 通道 REST(022 design §4)。
 *
 * 端点(对应后端 `agent_bridge/protocols/im/routes.py`):
 * - GET /v1/im/providers                        — 列已绑定 IM
 * - POST /v1/im/onboard/start                   — 启动扫码 onboard,返回 task_id
 * - GET /v1/im/onboard/{taskId}                 — 前端轮询拿 onboard 状态
 * - DELETE /v1/im/providers/{imType}/{bindId}   — 解绑
 *
 * 错误由 http 拦截器统一兜底为 FriendlyError。
 */

import { http } from "../http";

export type IMType = "qq";

export type ProviderStatus = "active" | "degraded" | "error" | "stopped";

export interface ProviderInfo {
  im_type: IMType;
  /** 原始绑定 id(QQ = user_openid);DELETE 接口路径用 raw。展示用 `bind_id_masked`。 */
  bind_id: string;
  /** 头 4 字符 + … + 尾 4 字符,前端可直接渲染。 */
  bind_id_masked: string;
  status: ProviderStatus;
}

export type OnboardStatus = "pending" | "qr_ready" | "success" | "failed";

export interface OnboardTaskState {
  task_id: string;
  im_type: IMType;
  status: OnboardStatus;
  qr_url: string | null;
  bind_id_masked: string | null;
  error: string | null;
}

export const imApi = {
  /** 列已绑定 IM 列表(脱敏)。 */
  listProviders(): Promise<ProviderInfo[]> {
    return http.get<unknown, ProviderInfo[]>("/v1/im/providers");
  },

  /** 启动扫码 onboard,返回 task_id 用于轮询。 */
  startOnboard(imType: IMType): Promise<{ task_id: string }> {
    return http.post<unknown, { task_id: string }>("/v1/im/onboard/start", {
      im_type: imType,
    });
  },

  /** 轮询 onboard 状态。 */
  getOnboardState(taskId: string): Promise<OnboardTaskState> {
    return http.get<unknown, OnboardTaskState>(`/v1/im/onboard/${taskId}`);
  },

  /** 解绑(stop provider + 删凭据);幂等。 */
  unbindProvider(
    imType: IMType,
    bindId: string,
  ): Promise<{ ok: boolean; found: boolean }> {
    return http.delete<unknown, { ok: boolean; found: boolean }>(
      `/v1/im/providers/${imType}/${bindId}`,
    );
  },
};
