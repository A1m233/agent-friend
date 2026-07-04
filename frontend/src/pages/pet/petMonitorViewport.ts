import { availableMonitors, type Monitor } from "@tauri-apps/api/window";

import type { PetRect, PetViewport } from "./petDisplayProfile";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";
import { isTauri } from "@/utils/tauri";

const EMPTY_VIEWPORT: PetViewport = { x: 0, y: 0, w: 0, h: 0 };

interface MonitorRect {
  position: { x: number; y: number };
  size: { width: number; height: number };
}

interface CssSize {
  w: number;
  h: number;
}

export function getPetOverlayViewport(): PetViewport {
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

function isUsableSize(size: CssSize): boolean {
  return Number.isFinite(size.w) && Number.isFinite(size.h) && size.w > 0 && size.h > 0;
}

function isUsableMonitorRect(monitor: MonitorRect): boolean {
  return (
    Number.isFinite(monitor.position.x) &&
    Number.isFinite(monitor.position.y) &&
    Number.isFinite(monitor.size.width) &&
    Number.isFinite(monitor.size.height) &&
    monitor.size.width > 0 &&
    monitor.size.height > 0
  );
}

export function computeMonitorViewports(
  monitors: MonitorRect[],
  overlaySize: CssSize,
): PetViewport[] {
  const usableMonitors = monitors.filter(isUsableMonitorRect);
  if (!usableMonitors.length || !isUsableSize(overlaySize)) return [];

  const minX = Math.min(...usableMonitors.map((monitor) => monitor.position.x));
  const minY = Math.min(...usableMonitors.map((monitor) => monitor.position.y));
  const maxX = Math.max(
    ...usableMonitors.map((monitor) => monitor.position.x + monitor.size.width),
  );
  const maxY = Math.max(
    ...usableMonitors.map((monitor) => monitor.position.y + monitor.size.height),
  );
  const unionW = maxX - minX;
  const unionH = maxY - minY;
  if (unionW <= 0 || unionH <= 0) return [];

  const scaleX = overlaySize.w / unionW;
  const scaleY = overlaySize.h / unionH;
  return usableMonitors
    .map((monitor) => ({
      x: (monitor.position.x - minX) * scaleX,
      y: (monitor.position.y - minY) * scaleY,
      w: monitor.size.width * scaleX,
      h: monitor.size.height * scaleY,
    }))
    .filter(isFiniteViewport);
}

function intersectionArea(rect: PetRect, viewport: PetViewport): number {
  const left = Math.max(rect.x, viewport.x);
  const right = Math.min(rect.x + rect.w, viewport.x + viewport.w);
  const top = Math.max(rect.y, viewport.y);
  const bottom = Math.min(rect.y + rect.h, viewport.y + viewport.h);
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}

function distanceToViewport(rect: PetRect, viewport: PetViewport): number {
  const centerX = rect.x + rect.w / 2;
  const centerY = rect.y + rect.h / 2;
  const nearestX = Math.min(viewport.x + viewport.w, Math.max(viewport.x, centerX));
  const nearestY = Math.min(viewport.y + viewport.h, Math.max(viewport.y, centerY));
  return Math.hypot(centerX - nearestX, centerY - nearestY);
}

export function resolvePetViewportForRect(
  rect: PetRect | null,
  monitorViewports: PetViewport[],
  fallback: PetViewport,
): PetViewport {
  const safeFallback = isFiniteViewport(fallback) ? fallback : EMPTY_VIEWPORT;
  const usableMonitorViewports = monitorViewports.filter(isFiniteViewport);
  if (!isFiniteRect(rect) || usableMonitorViewports.length === 0) {
    return safeFallback;
  }

  let bestViewport = usableMonitorViewports[0];
  let bestArea = -1;
  let bestDistance = Number.POSITIVE_INFINITY;

  for (const viewport of usableMonitorViewports) {
    const area = intersectionArea(rect, viewport);
    const distance = distanceToViewport(rect, viewport);
    if (area > bestArea || (area === bestArea && distance < bestDistance)) {
      bestViewport = viewport;
      bestArea = area;
      bestDistance = distance;
    }
  }
  return bestArea > 0 || Number.isFinite(bestDistance) ? bestViewport : safeFallback;
}

export async function loadPetMonitorViewports(
  overlayViewport: PetViewport = getPetOverlayViewport(),
): Promise<PetViewport[]> {
  if (!isTauri()) return [];
  const monitors: Monitor[] = await availableMonitors();
  return computeMonitorViewports(monitors, overlayViewport);
}
