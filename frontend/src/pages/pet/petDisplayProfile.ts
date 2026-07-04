import type { PetAvatarSize } from "@/lib/settings";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";

export type { PetAvatarSize };

export interface PetRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PetPoint {
  x: number;
  y: number;
}

export interface PetViewport {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type PetEdge = "left" | "right";

export interface PetDisplayProfile {
  avatarSize: PetAvatarSize;
  placementKey: string;
  scale: number;
  placementPoint: PetPoint | null;
  edge: PetEdge | null;
}

export const PET_NORMAL_SCALE = 1;
export const PET_MINI_SCALE = 0.5;
export const PET_EDGE_VISIBLE_RATIO = 0.62;
export const PET_VIEWPORT_MARGIN = 8;

export function getPetViewport(): PetViewport {
  if (typeof window === "undefined") {
    return { x: 0, y: 0, w: 0, h: 0 };
  }
  return {
    x: 0,
    y: 0,
    w: window.innerWidth,
    h: window.innerHeight,
  };
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) return Math.round((min + max) / 2);
  return Math.min(max, Math.max(min, value));
}

export function clampAvatarCenter(
  point: PetPoint,
  displaySize: { w: number; h: number },
  viewport: PetViewport,
  margin = PET_VIEWPORT_MARGIN,
): PetPoint {
  const minX = viewport.x + margin + displaySize.w / 2;
  const maxX = viewport.x + viewport.w - margin - displaySize.w / 2;
  const minY = viewport.y + margin + displaySize.h / 2;
  const maxY = viewport.y + viewport.h - margin - displaySize.h / 2;
  return {
    x: Math.round(clamp(point.x, minX, maxX)),
    y: Math.round(clamp(point.y, minY, maxY)),
  };
}

function nearestEdge(rect: PetRect, viewport: PetViewport): PetEdge {
  const left = Math.abs(rect.x - viewport.x);
  const right = Math.abs(viewport.x + viewport.w - (rect.x + rect.w));
  const entries: Array<[PetEdge, number]> = [
    ["left", left],
    ["right", right],
  ];
  entries.sort((a, b) => a[1] - b[1]);
  return entries[0][0];
}

export function computeEdgeDockPlacement(
  rect: PetRect,
  viewport: PetViewport,
  options: {
    scale?: number;
    visibleRatio?: number;
    margin?: number;
    edge?: PetEdge;
  } = {},
): { point: PetPoint; edge: PetEdge } {
  const scale = options.scale ?? PET_NORMAL_SCALE;
  const visibleRatio = options.visibleRatio ?? PET_EDGE_VISIBLE_RATIO;
  const margin = options.margin ?? PET_VIEWPORT_MARGIN;
  const edge = options.edge ?? nearestEdge(rect, viewport);
  const displaySize = { w: rect.w * scale, h: rect.h * scale };
  const center = {
    x: rect.x + rect.w / 2,
    y: rect.y + rect.h / 2,
  };
  const visibleOffsetX = displaySize.w * (visibleRatio - 0.5);

  if (edge === "left") {
    return {
      edge,
      point: {
        x: Math.round(viewport.x + margin + visibleOffsetX),
        y: clampAvatarCenter({ x: center.x, y: center.y }, displaySize, viewport, margin).y,
      },
    };
  }
  return {
    edge,
    point: {
      x: Math.round(viewport.x + viewport.w - margin - visibleOffsetX),
      y: clampAvatarCenter({ x: center.x, y: center.y }, displaySize, viewport, margin).y,
    },
  };
}

export function resolvePetDisplayProfile(
  input: {
    avatarSize: PetAvatarSize;
    rect: PetRect | null;
    viewport: PetViewport;
    previousAvatarSize?: PetAvatarSize | null;
  },
): PetDisplayProfile {
  const { avatarSize, rect, viewport, previousAvatarSize } = input;
  const safeRect = isFiniteRect(rect) ? rect : null;
  const safeViewport = isFiniteViewport(viewport) ? viewport : null;
  const scale = avatarSize === "mini" ? PET_MINI_SCALE : PET_NORMAL_SCALE;
  const placementKey = `${previousAvatarSize ?? "init"}->${avatarSize}`;

  if (
    avatarSize === "mini" &&
    previousAvatarSize === "normal" &&
    safeRect &&
    safeViewport
  ) {
    const { point, edge } = computeEdgeDockPlacement(safeRect, safeViewport, {
      scale,
    });
    return {
      avatarSize,
      placementKey,
      scale,
      placementPoint: point,
      edge,
    };
  }
  if (
    avatarSize === "normal" &&
    previousAvatarSize === "mini" &&
    safeRect &&
    safeViewport
  ) {
    const center = {
      x: safeRect.x + safeRect.w / 2,
      y: safeRect.y + safeRect.h / 2,
    };
    return {
      avatarSize,
      placementKey,
      scale,
      placementPoint: clampAvatarCenter(
        center,
        { w: safeRect.w, h: safeRect.h },
        safeViewport,
      ),
      edge: null,
    };
  }
  return {
    avatarSize,
    placementKey,
    scale,
    placementPoint: null,
    edge: null,
  };
}
