import type { PixelRegion } from "@/pet/petAlphaHitTest";

export interface PixelPoint {
  x: number;
  y: number;
}

export function sameRegion(a: PixelRegion | null, b: PixelRegion): boolean {
  return (
    a !== null &&
    Math.abs(a.x - b.x) < 0.5 &&
    Math.abs(a.y - b.y) < 0.5 &&
    Math.abs(a.w - b.w) < 0.5 &&
    Math.abs(a.h - b.h) < 0.5
  );
}

export function offsetRegionFromBase(
  region: PixelRegion,
  base: PixelRegion,
  scale: { x: number; y: number },
): PixelRegion {
  return {
    x: (region.x - base.x) / scale.x,
    y: (region.y - base.y) / scale.y,
    w: region.w / scale.x,
    h: region.h / scale.y,
  };
}

export function applyRegionOffset(
  base: PixelRegion,
  offset: PixelRegion,
  scale: { x: number; y: number },
): PixelRegion {
  return {
    x: base.x + offset.x * scale.x,
    y: base.y + offset.y * scale.y,
    w: offset.w * scale.x,
    h: offset.h * scale.y,
  };
}

export function offsetPointFromBase(
  point: PixelPoint,
  base: PixelRegion,
  scale: { x: number; y: number },
): PixelPoint {
  return {
    x: (point.x - base.x) / scale.x,
    y: (point.y - base.y) / scale.y,
  };
}

export function applyPointOffset(
  base: PixelRegion,
  offset: PixelPoint,
  scale: { x: number; y: number },
): PixelPoint {
  return {
    x: base.x + offset.x * scale.x,
    y: base.y + offset.y * scale.y,
  };
}

export function translateRegion(region: PixelRegion, dx: number, dy: number): PixelRegion {
  return {
    x: region.x + dx,
    y: region.y + dy,
    w: region.w,
    h: region.h,
  };
}

export function computePointDelta(
  point: PixelPoint | null,
  target: PixelPoint | null,
): { dx: number; dy: number } | null {
  if (!point || !target) return null;
  const dx = target.x - point.x;
  const dy = target.y - point.y;
  if (
    !Number.isFinite(dx) ||
    !Number.isFinite(dy) ||
    (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5)
  ) {
    return null;
  }
  return { dx, dy };
}

export function computeCenteringDelta(
  region: PixelRegion,
  point: PixelPoint | null,
): { dx: number; dy: number } | null {
  if (!point) return null;
  const dx = point.x - (region.x + region.w / 2);
  const dy = point.y - (region.y + region.h / 2);
  if (
    !Number.isFinite(dx) ||
    !Number.isFinite(dy) ||
    (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5)
  ) {
    return null;
  }
  return { dx, dy };
}

export function resolvePlacementAnchorRegion(
  base: PixelRegion,
  anchorOffset: PixelRegion | null,
  scale: { x: number; y: number },
): PixelRegion {
  return anchorOffset ? applyRegionOffset(base, anchorOffset, scale) : base;
}

export function resolvePlacementAnchorPoint(
  base: PixelRegion,
  anchorOffset: PixelPoint | null,
  scale: { x: number; y: number },
): PixelPoint | null {
  return anchorOffset ? applyPointOffset(base, anchorOffset, scale) : null;
}
