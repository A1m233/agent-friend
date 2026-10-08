import axios, { type AxiosInstance, type AxiosResponseTransformer } from "axios";
import { BRIDGE_BASE_URL, type LocalService } from "@/constants";
import { ensureLocalServiceReady } from "@/services/runtime";
import { recordHistoryPhase } from "@/services/historyObservation";

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
  if (axios.isCancel(err)) return { friendly: "读取已结束。", code: "cancelled" };
  // 这里只做归一化兜底；具体技术细节（status / 异常栈）不外泄，可在此集中上报/日志。
  if (axios.isAxiosError(err)) {
    if (err.code === "ECONNABORTED" || err.code === "ETIMEDOUT") {
      return { friendly: "读取用时较长，请重试。", code: "timeout" };
    }
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
    const navigationId = config.headers.get("x-agent-friend-navigation-id");
    const serviceStarted = performance.now();
    recordHistoryPhase(navigationId, "service-wait");
    await ensureLocalServiceReady(service);
    recordHistoryPhase(navigationId, "service-ready", { durationMs: performance.now() - serviceStarted });
    if (typeof navigationId === "string") {
      const transformers = config.transformResponse
        ? Array.isArray(config.transformResponse) ? config.transformResponse : [config.transformResponse]
        : [];
      const requestStarted = performance.now();
      const measured: AxiosResponseTransformer = function (data, headers, status) {
        const received = performance.now();
        const requestId = headers["x-agent-friend-request-id"];
        const contentLength = Number(headers["content-length"]);
        recordHistoryPhase(navigationId, "response-received", {
          durationMs: received - requestStarted,
          requestId: typeof requestId === "string" ? requestId : undefined,
          responseBytes: Number.isSafeInteger(contentLength) && contentLength >= 0 ? contentLength : undefined,
        });
        let result = data;
        try {
          for (const transform of transformers) result = transform.call(this, result, headers, status);
          return result;
        } finally {
          recordHistoryPhase(navigationId, "decode-finished", { durationMs: performance.now() - received });
        }
      };
      config.transformResponse = [measured];
    }
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
