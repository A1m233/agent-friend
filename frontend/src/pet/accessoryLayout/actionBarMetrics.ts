export const ACTION_BAR_PAGE_SIZE = 6;

/** Mirrors the existing size-8 / gap-1 / px-2 / py-1.5 component tokens. */
export function actionBarMetrics(buttonCount: number, spacing: number) {
  const slots = Math.min(buttonCount, ACTION_BAR_PAGE_SIZE);
  const arrows = buttonCount > ACTION_BAR_PAGE_SIZE ? 2 * 9 * spacing : 0;
  const content = slots * 8 * spacing + Math.max(0, slots - 1) * spacing + arrows;
  return {
    horizontal: { w: 4 * spacing + content, h: 11 * spacing },
    vertical: { w: 12 * spacing, h: 3 * spacing + content },
  };
}

/** The smallest operable bar shows one action and, when needed, both page arrows. */
export function actionBarMinimumMetrics(buttonCount: number, spacing: number) {
  const natural = actionBarMetrics(buttonCount, spacing);
  const content = (8 + (buttonCount > 1 ? 18 : 0)) * spacing;
  // Two complete actions need less room than replacing one with page arrows.
  return {
    horizontal: { w: Math.min(natural.horizontal.w, 4 * spacing + content), h: natural.horizontal.h },
    vertical: { w: natural.vertical.w, h: Math.min(natural.vertical.h, 3 * spacing + content) },
  };
}

export function actionBarButtonCount(dev: boolean, memoryInspector: boolean): number {
  return 8 + (dev ? 6 : 0) + (memoryInspector ? 1 : 0);
}

/** Reduced work areas still expose an action plus both paging controls. */
export function actionBarPageCapacity(buttonCount: number, extent: number, vertical: boolean, spacing: number): number {
  const padding = (vertical ? 3 : 4) * spacing;
  const unpaged = padding + buttonCount * 8 * spacing + Math.max(0, buttonCount - 1) * spacing;
  if (buttonCount <= ACTION_BAR_PAGE_SIZE && unpaged <= extent) return ACTION_BAR_PAGE_SIZE;
  const available = extent - padding - 18 * spacing;
  return Math.max(1, Math.min(ACTION_BAR_PAGE_SIZE, Math.floor((available + spacing) / (9 * spacing))));
}

export function readAccessorySpacing(): number {
  if (typeof document === "undefined") return 4;
  const style = getComputedStyle(document.documentElement);
  const token = style.getPropertyValue("--spacing").trim();
  const value = Number.parseFloat(token);
  if (!Number.isFinite(value) || value <= 0) return 4;
  return token.endsWith("rem") ? value * Number.parseFloat(style.fontSize) : value;
}
