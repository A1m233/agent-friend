export interface AnchorDiagnosticRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface AnchorDiagnosticPoint {
  x: number;
  y: number;
}

export interface AnchorDiagnosticDelta {
  dx: number;
  dy: number;
}

export interface AnchorDiagnosticCenters {
  spriteScreenCenterX?: number;
  visibleBoundsCenterX?: number;
  actionBarAnchorCenterX?: number;
  actionBarCenterX?: number;
  actionBarMinusAnchorX?: number;
  actionBarMinusSpriteX?: number;
  actionBarMinusVisibleX?: number;
}

export interface AnchorDiagnosticDisplayProfile {
  avatarSize: string;
  placementKey: string;
  scale: number;
  placementPoint: AnchorDiagnosticPoint | null;
  edge: string | null;
}

export interface AnchorDiagnosticDock {
  selectedViewportVisibleRatioX?: number;
  selectedViewportVisibleRatioY?: number;
  clippedLeft?: number;
  clippedRight?: number;
  clippedTop?: number;
  clippedBottom?: number;
}

export interface AnchorDiagnosticPlacement {
  layoutBounds?: AnchorDiagnosticRect | null;
  placementAnchor?: AnchorDiagnosticRect | null;
  placementAnchorPoint?: AnchorDiagnosticPoint | null;
  placementPoint?: AnchorDiagnosticPoint | null;
  delta?: AnchorDiagnosticDelta | null;
}

export interface PetAnchorDiagnostics {
  modelId?: string | null;
  backend?: string | null;
  dpr?: number;
  overlayViewport?: AnchorDiagnosticRect | null;
  selectedViewport?: AnchorDiagnosticRect | null;
  monitorViewports?: AnchorDiagnosticRect[];
  displayProfile?: AnchorDiagnosticDisplayProfile | null;
  spriteScreen?: AnchorDiagnosticRect | null;
  visibleBounds?: AnchorDiagnosticRect | null;
  actionBarAnchor?: AnchorDiagnosticRect | null;
  actionBarComputed?: AnchorDiagnosticRect | null;
  actionBarRect?: AnchorDiagnosticRect | null;
  dock?: AnchorDiagnosticDock;
  placement?: AnchorDiagnosticPlacement | null;
  centers: AnchorDiagnosticCenters;
  updatedAt: number;
}

declare global {
  interface Window {
    __AGENT_FRIEND_ANCHOR_DIAGNOSTICS__?: PetAnchorDiagnostics;
  }
}

export function centerX(rect: AnchorDiagnosticRect | null | undefined): number | undefined {
  return rect ? rect.x + rect.w / 2 : undefined;
}

function toFiniteRect(rect: AnchorDiagnosticRect | null | undefined) {
  if (!rect) return rect ?? null;
  if (
    !Number.isFinite(rect.x) ||
    !Number.isFinite(rect.y) ||
    !Number.isFinite(rect.w) ||
    !Number.isFinite(rect.h)
  ) {
    return null;
  }
  return rect;
}

function toFinitePoint(point: AnchorDiagnosticPoint | null | undefined) {
  if (!point) return point ?? null;
  return Number.isFinite(point.x) && Number.isFinite(point.y) ? point : null;
}

function toFiniteDelta(delta: AnchorDiagnosticDelta | null | undefined) {
  if (!delta) return delta ?? null;
  return Number.isFinite(delta.dx) && Number.isFinite(delta.dy) ? delta : null;
}

function toFiniteRectList(rects: AnchorDiagnosticRect[] | undefined) {
  return rects?.map((rect) => toFiniteRect(rect)).filter((rect) => rect !== null);
}

function computeDockDiagnostics(
  actionBarAnchor: AnchorDiagnosticRect | null | undefined,
  selectedViewport: AnchorDiagnosticRect | null | undefined,
): AnchorDiagnosticDock | undefined {
  if (!actionBarAnchor || !selectedViewport) return undefined;
  const visibleLeft = Math.max(actionBarAnchor.x, selectedViewport.x);
  const visibleRight = Math.min(
    actionBarAnchor.x + actionBarAnchor.w,
    selectedViewport.x + selectedViewport.w,
  );
  const visibleTop = Math.max(actionBarAnchor.y, selectedViewport.y);
  const visibleBottom = Math.min(
    actionBarAnchor.y + actionBarAnchor.h,
    selectedViewport.y + selectedViewport.h,
  );
  const visibleW = Math.max(0, visibleRight - visibleLeft);
  const visibleH = Math.max(0, visibleBottom - visibleTop);
  return {
    selectedViewportVisibleRatioX:
      actionBarAnchor.w > 0 ? visibleW / actionBarAnchor.w : undefined,
    selectedViewportVisibleRatioY:
      actionBarAnchor.h > 0 ? visibleH / actionBarAnchor.h : undefined,
    clippedLeft: Math.max(0, selectedViewport.x - actionBarAnchor.x),
    clippedRight: Math.max(
      0,
      actionBarAnchor.x + actionBarAnchor.w - (selectedViewport.x + selectedViewport.w),
    ),
    clippedTop: Math.max(0, selectedViewport.y - actionBarAnchor.y),
    clippedBottom: Math.max(
      0,
      actionBarAnchor.y + actionBarAnchor.h - (selectedViewport.y + selectedViewport.h),
    ),
  };
}

export function buildAnchorDiagnostics(
  previous: PetAnchorDiagnostics | undefined,
  patch: Partial<Omit<PetAnchorDiagnostics, "centers" | "updatedAt">>,
  updatedAt: number,
): PetAnchorDiagnostics {
  const next: PetAnchorDiagnostics = {
    centers: {},
    ...previous,
    ...patch,
    updatedAt,
    overlayViewport:
      "overlayViewport" in patch
        ? toFiniteRect(patch.overlayViewport)
        : previous?.overlayViewport,
    selectedViewport:
      "selectedViewport" in patch
        ? toFiniteRect(patch.selectedViewport)
        : previous?.selectedViewport,
    monitorViewports:
      "monitorViewports" in patch
        ? toFiniteRectList(patch.monitorViewports) ?? []
        : previous?.monitorViewports,
    displayProfile:
      "displayProfile" in patch
        ? patch.displayProfile
          ? {
              ...patch.displayProfile,
              placementPoint: toFinitePoint(patch.displayProfile.placementPoint),
            }
          : null
        : previous?.displayProfile,
    spriteScreen:
      "spriteScreen" in patch ? toFiniteRect(patch.spriteScreen) : previous?.spriteScreen,
    visibleBounds:
      "visibleBounds" in patch ? toFiniteRect(patch.visibleBounds) : previous?.visibleBounds,
    actionBarAnchor:
      "actionBarAnchor" in patch
        ? toFiniteRect(patch.actionBarAnchor)
        : previous?.actionBarAnchor,
    actionBarComputed:
      "actionBarComputed" in patch
        ? toFiniteRect(patch.actionBarComputed)
        : previous?.actionBarComputed,
    actionBarRect:
      "actionBarRect" in patch ? toFiniteRect(patch.actionBarRect) : previous?.actionBarRect,
    placement:
      "placement" in patch
        ? patch.placement
          ? {
              layoutBounds: toFiniteRect(patch.placement.layoutBounds),
              placementAnchor: toFiniteRect(patch.placement.placementAnchor),
              placementAnchorPoint: toFinitePoint(
                patch.placement.placementAnchorPoint,
              ),
              placementPoint: toFinitePoint(patch.placement.placementPoint),
              delta: toFiniteDelta(patch.placement.delta),
            }
          : null
        : previous?.placement,
  };
  next.dock = {
    ...previous?.dock,
    ...computeDockDiagnostics(next.actionBarAnchor, next.selectedViewport),
    ...patch.dock,
  };
  const actionBarCenter =
    centerX(next.actionBarRect) ?? centerX(next.actionBarComputed);
  const spriteScreenCenter = centerX(next.spriteScreen);
  const visibleBoundsCenter = centerX(next.visibleBounds);
  const actionBarAnchorCenter = centerX(next.actionBarAnchor);

  next.centers = {
    spriteScreenCenterX: spriteScreenCenter,
    visibleBoundsCenterX: visibleBoundsCenter,
    actionBarAnchorCenterX: actionBarAnchorCenter,
    actionBarCenterX: actionBarCenter,
    actionBarMinusAnchorX:
      actionBarCenter !== undefined && actionBarAnchorCenter !== undefined
        ? actionBarCenter - actionBarAnchorCenter
        : undefined,
    actionBarMinusSpriteX:
      actionBarCenter !== undefined && spriteScreenCenter !== undefined
        ? actionBarCenter - spriteScreenCenter
        : undefined,
    actionBarMinusVisibleX:
      actionBarCenter !== undefined && visibleBoundsCenter !== undefined
        ? actionBarCenter - visibleBoundsCenter
        : undefined,
  };
  return next;
}

export function publishAnchorDiagnostics(
  patch: Partial<Omit<PetAnchorDiagnostics, "centers" | "updatedAt">>,
): void {
  if (!import.meta.env.DEV || typeof window === "undefined") return;
  window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__ = buildAnchorDiagnostics(
    window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__,
    patch,
    performance.now(),
  );
  try {
    if (window.localStorage.getItem("agent-friend.debug.anchor") === "1") {
      console.info(
        "[pet][anchor-diagnostics]",
        window.__AGENT_FRIEND_ANCHOR_DIAGNOSTICS__,
      );
    }
  } catch {
    // localStorage may be unavailable in restricted contexts.
  }
}
