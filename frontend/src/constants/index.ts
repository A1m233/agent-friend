import { isTauri } from "@/utils/tauri";

export type LocalService = "bridge" | "voice";

const LOCAL_SERVICE_URLS: Record<LocalService, string> = {
  bridge: "http://127.0.0.1:18800",
  voice: "http://127.0.0.1:18900",
};

// 本机服务实际监听地址；Vite dev proxy 与 Tauri packaged runtime 共用这一份约定。
export const BRIDGE_DEFAULT_URL = LOCAL_SERVICE_URLS.bridge;
export const VOICE_DEFAULT_URL = LOCAL_SERVICE_URLS.voice;

interface LocalServiceUrlContext {
  dev: boolean;
  tauri: boolean;
  origin: string;
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
  context: LocalServiceUrlContext = {
    dev: import.meta.env.DEV,
    tauri: isTauri(),
    origin: currentWindowOrigin(),
  },
): string {
  return context.dev && !context.tauri ? "" : LOCAL_SERVICE_URLS[service];
}

/**
 * URL / WebSocket 构造必须使用绝对 base；纯 web dev 使用当前 Vite origin，
 * Tauri dev 与安装版直连 loopback。
 */
export function localServiceAbsoluteBaseUrl(
  service: LocalService,
  context: LocalServiceUrlContext = {
    dev: import.meta.env.DEV,
    tauri: isTauri(),
    origin: currentWindowOrigin(),
  },
): string {
  return context.dev && !context.tauri ? context.origin : LOCAL_SERVICE_URLS[service];
}

/**
 * bridge HTTP base。
 *
 * 纯 web 调试经 Vite proxy 保持同源；`tauri dev` 与安装版都显式直连由 Tauri
 * 拉起的 loopback bridge，避免开发态代理掩盖桌面 CORS / URL 差异。
 */
export const BRIDGE_BASE_URL = localServiceHttpBaseUrl("bridge");

// AG-UI 出口路径（006）。
export const AGUI_RUN_PATH = "/ag-ui/run";
