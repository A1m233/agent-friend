import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  DEFAULT_SETTINGS,
  applySettingCache,
  getSettingsSnapshot,
  replaceSettingsCache,
  type Settings,
} from "@/lib/settings";
import {
  syncDocumentTheme,
  type ThemeRoot,
} from "@/lib/settings/theme";
import { isTauri } from "@/utils/tauri";

export { syncDocumentTheme } from "@/lib/settings/theme";

export function syncDocumentThemeFromSettingsSnapshot(root?: ThemeRoot): void {
  syncDocumentTheme(getSettingsSnapshot().theme, root);
}

let registration: Promise<void> | null = null;
let unlistenSettings: UnlistenFn | null = null;
let generation = 0;

/** Release the page subscription during HMR; a disposed bootstrap cannot update a new page. */
export function unregisterSettingsListener(): void {
  generation += 1;
  unlistenSettings?.();
  unlistenSettings = null;
  registration = null;
}

/**
 * 各 HTML 入口在 createRoot() 之前等待一次，保证首屏使用当前设置。
 *
 * 监听主进程广播的 settings://changed，把"必须在 DOM 上立刻生效"的副作用
 * （theme → document.documentElement + TDesign 的 theme-mode）独立于 React 树执行。
 * 这样即使某个入口没有使用 useSetting，也能跟随全局主题变更。
 */
export function registerSettingsListener(): Promise<void> {
  if (registration) return registration;
  syncDocumentThemeFromSettingsSnapshot();
  if (!isTauri()) return Promise.resolve();
  const token = ++generation;
  let changeVersion = 0;
  registration = (async () => {
    try {
      const stop = await listen<{ key: string; value: unknown }>("settings://changed", (event) => {
        if (token !== generation) return;
        const key = event.payload.key as keyof Settings;
        if (key in DEFAULT_SETTINGS) {
          changeVersion += 1;
          applySettingCache(key, event.payload.value as Settings[typeof key]);
        }

        if (event.payload.key === "theme") syncDocumentTheme(event.payload.value);
      });
      if (token !== generation) {
        stop();
        return;
      }
      unlistenSettings = stop;
    } catch (error) {
      console.warn("settings change subscription unavailable", error);
    }
    if (token !== generation) return;

    // Subscribe first, then read the authoritative in-process state. The injected
    // initialization script contains app-start values even on WebView reload.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const beforeRead = changeVersion;
      try {
        const settings = await invoke<Settings>("get_setting");
        if (token !== generation) return;
        // A concurrently received change must not be overwritten by an older
        // response; reread instead of guessing which unversioned snapshot is newer.
        if (beforeRead !== changeVersion) continue;
        replaceSettingsCache(settings);
        syncDocumentThemeFromSettingsSnapshot();
        return;
      } catch (error) {
        console.warn("current settings unavailable; using cached bootstrap settings", error);
        return; // A failed local IPC must not leave every page permanently blank.
      }
    }
    console.warn("settings changed during bootstrap; retaining the latest received cache");
  })();
  return registration;
}

if (import.meta.hot) import.meta.hot.dispose(unregisterSettingsListener);
