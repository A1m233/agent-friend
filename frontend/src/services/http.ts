import axios, { type AxiosInstance } from "axios";
import { BRIDGE_BASE_URL } from "@/constants";

/**
 * 基础请求层（REST 通道）。
 *
 * 约定（见 010 design.md §4.2）：
 * - 上层只调 services 暴露的方法，不直接 import axios。
 * - 错误统一在拦截器兜底，吞掉技术细节，只把成功结果交给上层；
 *   失败时上层拿到归一化的 FriendlyError。
 */

export interface FriendlyError {
  /** 可直接展示给用户的拟人文案（不含技术细节）。 */
  friendly: string;
  /** 归一化错误码，便于上层分支处理（不展示给用户）。 */
  code?: string;
}

function toFriendlyError(err: unknown): FriendlyError {
  // 这里只做归一化兜底；具体技术细节（status / 异常栈）不外泄，可在此集中上报/日志。
  if (axios.isAxiosError(err)) {
    if (err.response) {
      return { friendly: "出了点小状况，我稍后再帮你试试。", code: "http_error" };
    }
    return { friendly: "网络好像不太顺畅，待会儿再聊？", code: "network" };
  }
  return { friendly: "我这边好像出了点小问题，能稍后再问我一遍吗？", code: "unknown" };
}

export function createHttp(baseURL: string = BRIDGE_BASE_URL): AxiosInstance {
  const http = axios.create({ baseURL, timeout: 30_000 });
  http.interceptors.response.use(
    (res) => res.data,
    (err) => Promise.reject(toFriendlyError(err)),
  );
  return http;
}

/** 默认实例（连接配置后续由 connection store 注入/重建）。 */
export const http = createHttp();
