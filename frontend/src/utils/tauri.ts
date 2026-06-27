/** 是否运行在 Tauri 桌面环境（区别于浏览器 web 调试）。 */
export function isTauri(): boolean {
  if (typeof globalThis === "undefined") return false;
  const tauriGlobal = globalThis as typeof globalThis & {
    isTauri?: boolean;
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  };
  return Boolean(
    tauriGlobal.isTauri ||
      tauriGlobal.__TAURI__ ||
      tauriGlobal.__TAURI_INTERNALS__,
  );
}
