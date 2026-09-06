import type { PetRect } from "@/pages/pet/petDisplayProfile";
import { isFiniteRect } from "@/pages/pet/petGeometry";
import type { AccessoryContext, PhysicalRect } from "./protocol";

export type AccessorySide = "top" | "right" | "bottom" | "left";
export interface Size { w: number; h: number }
export interface ActionBarLayout {
  orientation: "horizontal" | "vertical";
  left: number;
  top: number;
  width: number;
  height: number;
}
export interface AccessoryLayout {
  side: AccessorySide;
  actionBar: ActionBarLayout;
  bubble: PetRect | null;
  bubbleLimit: Size;
  tooltipSide: AccessorySide;
  tooltipBoundary: PetRect | null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(min, max), Math.max(min, value));
}

function area(rect: PetRect, viewport: PetRect): number {
  return Math.max(0, Math.min(rect.x + rect.w, viewport.x + viewport.w) - Math.max(rect.x, viewport.x)) *
    Math.max(0, Math.min(rect.y + rect.h, viewport.y + viewport.h) - Math.max(rect.y, viewport.y));
}

export function physicalToPetCss(rect: PhysicalRect, origin: { x: number; y: number }, dpr: number): PetRect {
  return { x: (rect.x - origin.x) / dpr, y: (rect.y - origin.y) / dpr, w: rect.width / dpr, h: rect.height / dpr };
}

export function petCssToPhysical(rect: PetRect, origin: { x: number; y: number }, dpr: number): PhysicalRect {
  return {
    x: Math.round(origin.x + rect.x * dpr), y: Math.round(origin.y + rect.y * dpr),
    width: Math.max(1, Math.floor(rect.w * dpr)), height: Math.max(1, Math.floor(rect.h * dpr)),
  };
}

/** Select by full monitor bounds, then return its work area in the single overlay CSS space. */
export function selectAccessoryWorkArea(anchor: PetRect, context: AccessoryContext, dpr: number): PetRect | null {
  if (!isFiniteRect(anchor) || !Number.isFinite(dpr) || dpr <= 0) return null;
  const center = { x: anchor.x + anchor.w / 2, y: anchor.y + anchor.h / 2 };
  const candidates = context.monitors.map((monitor) => {
    const bounds = physicalToPetCss(monitor, context.origin, dpr);
    const distance = Math.hypot(center.x - clamp(center.x, bounds.x, bounds.x + bounds.w), center.y - clamp(center.y, bounds.y, bounds.y + bounds.h));
    return { monitor, overlap: area(anchor, bounds), distance };
  }).sort((a, b) => b.overlap - a.overlap || a.distance - b.distance);
  const selected = candidates[0]?.monitor;
  if (!selected) return null;
  const workArea = physicalToPetCss(selected.workArea, context.origin, dpr);
  return isFiniteRect(workArea) ? workArea : null;
}

export function computeAccessoryLayout(input: {
  anchor: PetRect;
  workArea: PetRect;
  sizes: { horizontal: Size; vertical: Size };
  minimumSizes: { horizontal: Size; vertical: Size };
  bubble: Size | null;
  minimumBubbleSize: Size;
  gap: number;
  previousSide?: AccessorySide | null;
}): AccessoryLayout | null {
  const { anchor, workArea, sizes, minimumSizes, bubble, minimumBubbleSize, gap } = input;
  if (![anchor, workArea].every(isFiniteRect) || !Number.isFinite(gap) || gap < 0 ||
      ![sizes.horizontal, sizes.vertical, minimumSizes.horizontal, minimumSizes.vertical, minimumBubbleSize, ...(bubble ? [bubble] : [])]
        .every((s) => Number.isFinite(s.w) && Number.isFinite(s.h) && s.w > 0 && s.h > 0) ||
      minimumSizes.horizontal.w > sizes.horizontal.w || minimumSizes.horizontal.h > sizes.horizontal.h ||
      minimumSizes.vertical.w > sizes.vertical.w || minimumSizes.vertical.h > sizes.vertical.h) return null;
  const inner = { x: workArea.x + gap, y: workArea.y + gap, w: workArea.w - gap * 2, h: workArea.h - gap * 2 };
  if (!isFiniteRect(inner)) return null;
  const cx = anchor.x + anchor.w / 2;
  const cy = anchor.y + anchor.h / 2;
  const lateral: AccessorySide[] = cx <= inner.x + inner.w / 2 ? ["right", "left"] : ["left", "right"];
  // Judge the natural centered bar before clamping. Otherwise a horizontal bar
  // always appears to fit after being shifted away from either screen edge.
  const edgeThreshold = Math.max(sizes.vertical.w * 2, anchor.w / 2 + sizes.vertical.w, sizes.horizontal.w / 2 + gap);
  const edgeDistance = Math.min(cx - workArea.x, workArea.x + workArea.w - cx);
  // Retain only a small edge band, not the previous side across the whole screen.
  const nearEdge = edgeDistance <= edgeThreshold + (input.previousSide === lateral[0] ? gap : 0);
  const sides: AccessorySide[] = nearEdge ? [lateral[0], "top", "bottom", lateral[1]] : ["top", "bottom", ...lateral];
  const candidates = sides.flatMap((side): AccessoryLayout[] => {
    const vertical = side === "left" || side === "right";
    const naturalBar = vertical ? sizes.vertical : sizes.horizontal;
    const minimumBar = vertical ? minimumSizes.vertical : minimumSizes.horizontal;
    // Shortening may introduce pagination, so retain space for its controls too.
    const bar = { w: vertical ? naturalBar.w : Math.min(naturalBar.w, inner.w), h: vertical ? Math.min(naturalBar.h, inner.h) : naturalBar.h };
    if (bar.w < minimumBar.w || bar.h < minimumBar.h) return [];
    const x = side === "right" ? anchor.x + anchor.w + gap : side === "left" ? anchor.x - gap - bar.w : clamp(cx - bar.w / 2, inner.x, inner.x + inner.w - bar.w);
    const y = side === "bottom" ? anchor.y + anchor.h + gap : side === "top" ? anchor.y - gap - bar.h : clamp(cy - bar.h / 2, inner.y, inner.y + inner.h - bar.h);
    const barRect = { x, y, ...bar };
    if (x < inner.x || y < inner.y || x + bar.w > inner.x + inner.w || y + bar.h > inner.y + inner.h) return [];
    const space = side === "top" ? y - inner.y : side === "bottom" ? inner.y + inner.h - y - bar.h : side === "left" ? x - inner.x : inner.x + inner.w - x - bar.w;
    // Keep the normal tooltip lane stable whenever a native bubble is present.
    // gap = two --spacing units: 48px high / 128px wide at the default scale.
    const tooltipExtent = (vertical ? 16 : 6) * gap;
    const bubbleGap = tooltipExtent + gap * 2;
    const limit = { w: Math.max(0, vertical ? space - bubbleGap : inner.w), h: Math.max(0, vertical ? inner.h : space - bubbleGap) };

    const tooltipRect = (extent: number): PetRect => ({
      x: side === "right" ? x + bar.w + gap : side === "left" ? x - gap - extent : inner.x,
      y: side === "bottom" ? y + bar.h + gap : side === "top" ? y - gap - extent : inner.y,
      w: vertical ? extent : inner.w,
      h: vertical ? inner.h : extent,
    });
    // Shared TooltipContent: text-xs line 16 + py-1.5 total 12 = 28px.
    // Width includes px-3 padding and enough text to remain useful after wrapping.
    const readableTooltip = (rect: PetRect) => isFiniteRect(rect) &&
      rect.w >= (vertical ? 16 : 15) * gap && rect.h >= 3.5 * gap;
    const normalTooltip = tooltipRect(tooltipExtent);
    const width = bubble ? Math.min(bubble.w, limit.w) : 0;
    const height = bubble ? Math.min(bubble.h, limit.h) : 0;
    const canShow = bubble !== null && readableTooltip(normalTooltip) &&
      width >= Math.min(bubble.w, minimumBubbleSize.w) && height >= Math.min(bubble.h, minimumBubbleSize.h);
    const bubbleRect = canShow ? {
      x: side === "right" ? x + bar.w + bubbleGap : side === "left" ? x - bubbleGap - width : clamp(cx - width / 2, inner.x, inner.x + inner.w - width),
      y: side === "bottom" ? y + bar.h + bubbleGap : side === "top" ? y - bubbleGap - height : clamp(cy - height / 2, inner.y, inner.y + inner.h - height),
      w: width, h: height,
    } : null;
    // With no shown bubble, the lane can shrink to its readable minimum, and
    // there is no phantom bubble or second gap influencing the direction.
    const availableTooltip = tooltipRect(Math.max(0, Math.min(tooltipExtent, space - gap)));
    const tooltipBoundary = canShow ? normalTooltip : readableTooltip(availableTooltip) ? availableTooltip : null;
    return [{ side, actionBar: { orientation: vertical ? "vertical" : "horizontal", left: barRect.x, top: barRect.y, width: bar.w, height: bar.h }, bubble: bubbleRect, bubbleLimit: limit, tooltipSide: side, tooltipBoundary }];
  });
  // Readable content qualifies a candidate; extra natural height never outranks
  // the spatial order. If none can show a bubble, keep usable controls visible.
  return (bubble ? candidates.find((candidate) => candidate.bubble !== null) : undefined)
    ?? candidates.find((candidate) => candidate.tooltipBoundary !== null)
    ?? candidates[0]
    ?? null;
}
