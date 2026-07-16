import type { PetPoint, PetRect, PetViewport } from "./petDisplayProfile";

const GEOMETRY_EPSILON = 0.5;

export function isFinitePoint(point: PetPoint | null | undefined): point is PetPoint {
  return (
    !!point &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y)
  );
}

export function isFiniteRect(rect: PetRect | null | undefined): rect is PetRect {
  return (
    !!rect &&
    Number.isFinite(rect.x) &&
    Number.isFinite(rect.y) &&
    Number.isFinite(rect.w) &&
    Number.isFinite(rect.h) &&
    rect.w > 0 &&
    rect.h > 0
  );
}

export function isFiniteViewport(
  viewport: PetViewport | null | undefined,
): viewport is PetViewport {
  return isFiniteRect(viewport);
}

export function samePoint(
  a: PetPoint | null | undefined,
  b: PetPoint | null | undefined,
  epsilon = GEOMETRY_EPSILON,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return Math.abs(a.x - b.x) < epsilon && Math.abs(a.y - b.y) < epsilon;
}

export function sameRect(
  a: PetRect | null | undefined,
  b: PetRect | null | undefined,
  epsilon = GEOMETRY_EPSILON,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    Math.abs(a.x - b.x) < epsilon &&
    Math.abs(a.y - b.y) < epsilon &&
    Math.abs(a.w - b.w) < epsilon &&
    Math.abs(a.h - b.h) < epsilon
  );
}

export function sameViewportList(
  a: PetViewport[],
  b: PetViewport[],
  epsilon = GEOMETRY_EPSILON,
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return a.every((viewport, index) => sameRect(viewport, b[index], epsilon));
}

export function centerPointOfRect(rect: PetRect): PetPoint {
  return {
    x: rect.x + rect.w / 2,
    y: rect.y + rect.h / 2,
  };
}

/**
 * Placement is applied against the visible/action-bar anchor when a runtime can
 * provide one (notably Spine). The layout bounds can have a different center,
 * so using spriteScreen alone leaves a completed one-shot intent alive forever.
 */
export function hasReachedPlacementPoint(
  target: PetPoint,
  actionBarAnchor: PetRect | null | undefined,
  spriteScreen: PetRect | null | undefined,
): boolean {
  const anchor = isFiniteRect(actionBarAnchor)
    ? actionBarAnchor
    : isFiniteRect(spriteScreen)
      ? spriteScreen
      : null;
  return anchor ? samePoint(centerPointOfRect(anchor), target) : false;
}
