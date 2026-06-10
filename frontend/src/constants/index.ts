// bridge 实际监听地址（见 006 / scripts/bridge）；作为 vite dev 代理的 target。
export const BRIDGE_DEFAULT_URL = "http://127.0.0.1:18800";

/**
 * 前端发请求用的 base：**同源相对**（空串）。
 *
 * bridge 不带 CORS 头（且本期零侵入不给它加），所以浏览器 / Tauri webview 不能直连。
 * dev（web 调试与 `tauri dev` 都经 vite:1420）下由 vite proxy 把 /v1、/ag-ui、/healthz
 * 转发到 BRIDGE_DEFAULT_URL，从而同源、免 CORS（见 vite.config.ts）。
 * 生产打包不在本期范围（requirement §3），届时另接 Tauri http 通道。
 */
export const BRIDGE_BASE_URL = "";

// AG-UI 出口路径（006）。
export const AGUI_RUN_PATH = "/ag-ui/run";
