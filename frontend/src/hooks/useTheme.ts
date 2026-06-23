import * as React from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "agent-friend-theme";
const DEFAULT_THEME: Theme = "light";

function getStoredTheme(): Theme {
  if (typeof window === "undefined") return DEFAULT_THEME;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === "dark" ? "dark" : DEFAULT_THEME;
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("theme", theme);
}

/**
 * 主题状态 hook。
 *
 * - 读取/持久化到 localStorage（key = agent-friend-theme）
 * - 切换时同步到 html[theme]，保证项目 CSS 变量换肤生效
 * - 监听 storage 事件，支持多标签页同步
 */
export function useTheme() {
  const [theme, setThemeState] = React.useState<Theme>(() => getStoredTheme());

  const setTheme = React.useCallback((next: Theme) => {
    window.localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
    setThemeState(next);
  }, []);

  React.useEffect(() => {
    // 首次挂载时确保 html[theme] 与 state 一致（防御外部脚本改动）
    applyTheme(theme);

    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      const next = event.newValue === "dark" ? "dark" : "light";
      applyTheme(next);
      setThemeState(next);
    };

    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [theme]);

  return { theme, setTheme };
}

/**
 * 在应用挂载前调用，避免首屏闪屏。
 * 直接读 localStorage 并设置 html[theme]，不依赖 React。
 */
export function initTheme() {
  if (typeof window === "undefined") return;
  const theme = getStoredTheme();
  applyTheme(theme);
}
