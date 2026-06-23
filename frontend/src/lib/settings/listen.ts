import { listen } from "@tauri-apps/api/event";

/**
 * 各 HTML 入口在 createRoot() 之前调用一次。
 *
 * 监听主进程广播的 settings://changed，把"必须在 DOM 上立刻生效"的副作用
 * （目前只有 theme → document.documentElement.setAttribute）独立于 React 树执行。
 * 这样即使某个入口没有使用 useSetting，也能跟随全局主题变更。
 */
export function registerSettingsListener(): void {
  void listen<{ key: string; value: unknown }>("settings://changed", (event) => {
    if (event.payload.key === "theme") {
      const theme = event.payload.value === "dark" ? "dark" : "light";
      document.documentElement.setAttribute("theme", theme);
    }
  });
}
