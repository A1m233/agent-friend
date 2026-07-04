export type ThemeRoot = Pick<HTMLElement, "setAttribute" | "removeAttribute"> & {
  style?: Pick<CSSStyleDeclaration, "colorScheme">;
};

export function normalizeTheme(value: unknown): "light" | "dark" {
  return value === "dark" ? "dark" : "light";
}

export function syncDocumentTheme(value: unknown, root?: ThemeRoot | null): void {
  const target =
    root ?? (typeof document === "undefined" ? null : document.documentElement);
  if (!target) return;

  const theme = normalizeTheme(value);
  target.setAttribute("theme", theme);
  if (target.style) {
    target.style.colorScheme = theme;
  }

  if (theme === "dark") {
    target.setAttribute("theme-mode", "dark");
  } else {
    target.removeAttribute("theme-mode");
  }
}
