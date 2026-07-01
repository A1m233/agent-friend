import { computeActionBarPosition } from "./computeActionBarPosition";

export type ActionBarOrientation = "horizontal" | "vertical";

export interface ActionBarLayout {
  orientation: ActionBarOrientation;
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface ActionBarRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ActionBarViewport {
  w: number;
  h: number;
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) return Math.round((min + max) / 2);
  return Math.min(max, Math.max(min, value));
}

function isNearVerticalEdge(
  anchor: ActionBarRect,
  viewport: ActionBarViewport,
  threshold: number,
): boolean {
  const centerX = anchor.x + anchor.w / 2;
  return centerX <= threshold || viewport.w - centerX <= threshold;
}

function horizontalFitsViewport(
  left: number,
  width: number,
  viewport: ActionBarViewport,
  margin: number,
): boolean {
  return left >= margin && left + width <= viewport.w - margin;
}

export function computeActionBarLayout(
  anchor: ActionBarRect,
  sizes: {
    horizontal: { w: number; h: number };
    vertical: { w: number; h: number };
  },
  viewport: ActionBarViewport,
  margin = 8,
): ActionBarLayout {
  const horizontalPosition = computeActionBarPosition(anchor, sizes.horizontal, margin);
  const shouldUseVertical =
    isNearVerticalEdge(anchor, viewport, Math.max(96, anchor.w / 2 + sizes.vertical.w)) ||
    !horizontalFitsViewport(horizontalPosition.left, sizes.horizontal.w, viewport, margin);

  if (!shouldUseVertical) {
    return {
      orientation: "horizontal",
      left: horizontalPosition.left,
      top: horizontalPosition.top,
      width: sizes.horizontal.w,
      height: sizes.horizontal.h,
    };
  }

  const anchorCenterX = anchor.x + anchor.w / 2;
  const anchorCenterY = anchor.y + anchor.h / 2;
  const left =
    anchorCenterX <= viewport.w / 2
      ? anchor.x + anchor.w + margin
      : anchor.x - sizes.vertical.w - margin;
  const top = clamp(
    Math.round(anchorCenterY - sizes.vertical.h / 2),
    margin,
    viewport.h - margin - sizes.vertical.h,
  );

  return {
    orientation: "vertical",
    left: Math.round(clamp(left, margin, viewport.w - margin - sizes.vertical.w)),
    top,
    width: sizes.vertical.w,
    height: sizes.vertical.h,
  };
}
