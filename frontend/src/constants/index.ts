import { isTauri } from "@/utils/tauri";

export type LocalService = "bridge" | "voice";

const LOCAL_SERVICE_URLS: Record<LocalService, string> = {
  bridge: "http://127.0.0.1:18800",
  voice: "http://127.0.0.1:18900",
};

interface LocalServiceEndpoints extends Record<LocalService, string> {
  profileId: string;
}

declare global {
  interface Window {
    __AGENT_FRIEND_LOCAL_SERVICES__?: LocalServiceEndpoints;
  }
}

interface LocalServiceUrlContext {
  dev: boolean;
  tauri: boolean;
  origin: string;
  endpoints?: LocalServiceEndpoints;
}

function desktopServiceUrl(service: LocalService, context: LocalServiceUrlContext): string {
  if (!context.tauri) return LOCAL_SERVICE_URLS[service];
  const endpoints = context.endpoints;
  if (!endpoints?.profileId || ![endpoints.bridge, endpoints.voice].every((value) => {
    if (typeof value !== "string" || !/^http:\/\/127\.0\.0\.1:\d+$/.test(value)) return false;
    const port = Number(value.split(":").at(-1));
    return Number.isInteger(port) && port > 0 && port <= 65535;
  }) || endpoints.bridge === endpoints.voice) {
    throw new Error("Desktop local service configuration is missing or invalid");
  }
  return endpoints[service];
}

function currentContext(): LocalServiceUrlContext {
  return {
    dev: import.meta.env.DEV,
    tauri: isTauri(),
    origin: currentWindowOrigin(),
    endpoints: typeof window === "undefined" ? undefined : window.__AGENT_FRIEND_LOCAL_SERVICES__,
  };
}

function currentWindowOrigin(): string {
  if (typeof window === "undefined") return "http://localhost";
  return window.location?.origin ?? "http://localhost";
}

/**
 * Axios 等允许相对路径的 HTTP client 使用：纯 web dev 走 Vite 同源代理，
 * Tauri dev 与安装版都直连 loopback，确保桌面开发态覆盖生产网络拓扑。
 */
export function localServiceHttpBaseUrl(
  service: LocalService,
  context: LocalServiceUrlContext = currentContext(),
): string {
  return context.dev && !context.tauri ? "" : desktopServiceUrl(service, context);
}

/**
 * URL / WebSocket 构造必须使用绝对 base；纯 web dev 使用当前 Vite origin，
 * Tauri dev 与安装版直连 loopback。
 */
export function localServiceAbsoluteBaseUrl(
  service: LocalService,
  context: LocalServiceUrlContext = currentContext(),
): string {
  return context.dev && !context.tauri ? context.origin : desktopServiceUrl(service, context);
}

export const BRIDGE_DEFAULT_URL = desktopServiceUrl("bridge", currentContext());
export const VOICE_DEFAULT_URL = desktopServiceUrl("voice", currentContext());

/**
 * bridge HTTP base。
 *
 * 纯 web 调试经 Vite proxy 保持同源；`tauri dev` 与安装版都显式直连由 Tauri
 * 拉起的 loopback bridge，避免开发态代理掩盖桌面 CORS / URL 差异。
 */
export const BRIDGE_BASE_URL = localServiceHttpBaseUrl("bridge");

export function resolveBridgeUrl(
  url: string,
  bridgeBaseUrl: string = BRIDGE_BASE_URL,
): string {
  if (/^[a-z][a-z\d+.-]*:/i.test(url) || url.startsWith("//")) return url;
  const path = url.startsWith("/") ? url : `/${url}`;
  return `${bridgeBaseUrl}${path}`;
}

// AG-UI 出口路径（006）。
export const AGUI_RUN_PATH = "/ag-ui/run";
