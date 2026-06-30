export interface AnchorDiagnosticRect {
  x: number;
  y: number;
  w: number;
  h: number;
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

export interface PetAnchorDiagnostics {
  modelId?: string | null;
  backend?: string | null;
  spriteScreen?: AnchorDiagnosticRect | null;
  visibleBounds?: AnchorDiagnosticRect | null;
  actionBarAnchor?: AnchorDiagnosticRect | null;
  actionBarComputed?: AnchorDiagnosticRect | null;
  actionBarRect?: AnchorDiagnosticRect | null;
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
}
