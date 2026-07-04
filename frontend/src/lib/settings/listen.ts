import { listen } from "@tauri-apps/api/event";
import {
  DEFAULT_SETTINGS,
  applySettingCache,
  getSettingsSnapshot,
  type Settings,
} from "@/lib/settings";
import {
  syncDocumentTheme,
  type ThemeRoot,
} from "@/lib/settings/theme";

export { syncDocumentTheme } from "@/lib/settings/theme";

export function syncDocumentThemeFromSettingsSnapshot(root?: ThemeRoot): void {
  syncDocumentTheme(getSettingsSnapshot().theme, root);
}

/**
 * 各 HTML 入口在 createRoot() 之前调用一次。
 *
 * 监听主进程广播的 settings://changed，把"必须在 DOM 上立刻生效"的副作用
 * （theme → document.documentElement + TDesign 的 theme-mode）独立于 React 树执行。
 * 这样即使某个入口没有使用 useSetting，也能跟随全局主题变更。
 */
export function registerSettingsListener(): void {
  syncDocumentThemeFromSettingsSnapshot();

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
