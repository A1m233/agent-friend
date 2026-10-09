import axios, { type AxiosInstance } from "axios";
import { BRIDGE_BASE_URL, type LocalService } from "@/constants";
import { ensureLocalServiceReady } from "@/services/runtime";

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

export function friendlyErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object" && "friendly" in error) {
    const friendly = (error as Partial<FriendlyError>).friendly;
    if (typeof friendly === "string" && friendly.trim()) return friendly;
  }
  return "我这边好像出了点小问题，能稍后再试一次吗？";
}

export function requireJsonRestResponse(data: unknown, contentType: unknown): void {
  if (data === null || data === undefined || data === "") return;
  if (typeof contentType === "string" && contentType.toLowerCase().includes("json")) return;
  throw {
    friendly: "本机服务返回了无法识别的内容，请重启应用后再试。",
    code: "invalid_response",
  } satisfies FriendlyError;
}

function backendDetailOf(data: unknown): FriendlyError | null {
  if (!data || typeof data !== "object") return null;
  if ("error_kind" in data && typeof data.error_kind === "string" && "message" in data && typeof data.message === "string") {
    return { friendly: data.message, code: data.error_kind };
  }
  const envelope = "error" in data ? (data as { error?: unknown }).error : null;
  if (envelope && typeof envelope === "object") {
    const message =
      "message" in envelope && typeof envelope.message === "string"
        ? envelope.message
        : null;
    const code =
      "code" in envelope && typeof envelope.code === "string"
        ? envelope.code
        : undefined;
    if (message) return { friendly: message, code };
  }
  const detail = "detail" in data ? (data as { detail?: unknown }).detail : null;
  if (!detail || typeof detail !== "object") return null;

  const message =
    "message" in detail && typeof detail.message === "string"
      ? detail.message
      : null;
  const code =
    "error" in detail && typeof detail.error === "string" ? detail.error : undefined;
  if (!message) return null;
  return { friendly: message, code };
}

export function toFriendlyError(err: unknown): FriendlyError {
  // 这里只做归一化兜底；具体技术细节（status / 异常栈）不外泄，可在此集中上报/日志。
  if (axios.isAxiosError(err)) {
    if (err.response) {
      const backendDetail = backendDetailOf(err.response.data);
      if (backendDetail) return backendDetail;
      return { friendly: "出了点小状况，我稍后再帮你试试。", code: "http_error" };
    }
    return { friendly: "网络好像不太顺畅，待会儿再聊？", code: "network" };
  }
  return { friendly: "我这边好像出了点小问题，能稍后再问我一遍吗？", code: "unknown" };
}

export function createHttp(
  baseURL: string = BRIDGE_BASE_URL,
  service: LocalService = "bridge",
): AxiosInstance {
  const http = axios.create({ baseURL, timeout: 30_000 });
  http.interceptors.request.use(async (config) => {
    await ensureLocalServiceReady(service);
    return config;
  });
  http.interceptors.response.use(
    (res) => {
      requireJsonRestResponse(res.data, res.headers["content-type"]);
      return res.data;
    },
    (err) => Promise.reject(toFriendlyError(err)),
  );
  return http;
}

/** 默认实例（连接配置后续由 connection store 注入/重建）。 */
export const http = createHttp();
