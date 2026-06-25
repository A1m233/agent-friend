import { listen } from "@tauri-apps/api/event";
import { DEFAULT_SETTINGS, applySettingCache, type Settings } from "@/lib/settings";

type ThemeRoot = Pick<HTMLElement, "setAttribute" | "removeAttribute">;

export function syncDocumentTheme(value: unknown, root: ThemeRoot = document.documentElement): void {
  const theme = value === "dark" ? "dark" : "light";
  root.setAttribute("theme", theme);

  if (theme === "dark") {
    root.setAttribute("theme-mode", "dark");
  } else {
    root.removeAttribute("theme-mode");
  }
}

/**
 * 各 HTML 入口在 createRoot() 之前调用一次。
 *
 * 监听主进程广播的 settings://changed，把"必须在 DOM 上立刻生效"的副作用
 * （theme → document.documentElement + TDesign 的 theme-mode）独立于 React 树执行。
 * 这样即使某个入口没有使用 useSetting，也能跟随全局主题变更。
 */
export function registerSettingsListener(): void {
  void listen<{ key: string; value: unknown }>("settings://changed", (event) => {
    const key = event.payload.key as keyof Settings;
    if (key in DEFAULT_SETTINGS) {
      applySettingCache(key, event.payload.value as Settings[typeof key]);
    }

    if (event.payload.key === "theme") {
      syncDocumentTheme(event.payload.value);
    }
  });
}
