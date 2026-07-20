import { invoke } from "@tauri-apps/api/core";
import { availableMonitors, cursorPosition, type Monitor } from "@tauri-apps/api/window";

import type { PetPoint, PetRect, PetViewport } from "./petDisplayProfile";
import { isFiniteRect, isFiniteViewport } from "./petGeometry";
import { isTauri } from "@/utils/tauri";

const EMPTY_VIEWPORT: PetViewport = { x: 0, y: 0, w: 0, h: 0 };

interface MonitorRect {
  position: { x: number; y: number };
  size: { width: number; height: number };
}

export interface CssSize {
  w: number;
  h: number;
}

export interface OverlayPhysicalBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MonitorPhysicalSnapshot {
  x: number;
  y: number;
  width: number;
  height: number;
  scaleMilli?: number | null;
}

export interface MonitorViewportSnapshot extends PetViewport {
  monitor: MonitorPhysicalSnapshot;
  index: number;
}

export interface PetPointMonitorRebaseDiagnostics {
  previousViewports: MonitorViewportSnapshot[];
  nextViewports: MonitorViewportSnapshot[];
  previousViewport: MonitorViewportSnapshot | null;
  nextViewport: MonitorViewportSnapshot | null;
  relativeAnchor: PetPoint | null;
}

export interface PetPointMonitorRebaseResult {
  point: PetPoint | null;
  diagnostics: PetPointMonitorRebaseDiagnostics;
}

export interface PetMonitorRelativeAnchor {
  monitor: MonitorPhysicalSnapshot;
  relativeAnchor: PetPoint;
}

export interface PetUiPersistence {
  anchor?: PetMonitorRelativeAnchor | null;
}

export interface PetDisplayPhysicalSnapshot {
  bounds: OverlayPhysicalBounds;
  monitors: MonitorPhysicalSnapshot[];
}

export interface PetInitialPlacementInput {
  cursor: PetPoint;
  bounds: OverlayPhysicalBounds;
  monitors: MonitorPhysicalSnapshot[];
  overlaySize: CssSize;
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

function computeMonitorViewportsWithinBounds(
  monitors: MonitorPhysicalSnapshot[],
  bounds: OverlayPhysicalBounds,
  overlaySize: CssSize,
): MonitorViewportSnapshot[] {
  const usableMonitors = monitors.filter(isUsablePhysicalMonitor);
  if (
    !usableMonitors.length ||
    !isUsablePhysicalBounds(bounds) ||
    !isUsableSize(overlaySize)
  ) {
    return [];
  }

  const scaleX = overlaySize.w / bounds.width;
  const scaleY = overlaySize.h / bounds.height;
  return usableMonitors
    .map((monitor, index) => ({
      x: (monitor.x - bounds.x) * scaleX,
      y: (monitor.y - bounds.y) * scaleY,
      w: monitor.width * scaleX,
      h: monitor.height * scaleY,
      monitor,
      index,
    }))
    .filter(isFiniteViewport);
}

function computeOverlayPhysicalBounds(
  monitors: MonitorPhysicalSnapshot[],
): OverlayPhysicalBounds | null {
  const usableMonitors = monitors.filter(isUsablePhysicalMonitor);
  if (!usableMonitors.length) return null;

  const minX = Math.min(...usableMonitors.map((monitor) => monitor.x));
  const minY = Math.min(...usableMonitors.map((monitor) => monitor.y));
  const maxX = Math.max(...usableMonitors.map((monitor) => monitor.x + monitor.width));
  const maxY = Math.max(...usableMonitors.map((monitor) => monitor.y + monitor.height));
  const width = maxX - minX;
  const height = maxY - minY;
  if (width <= 0 || height <= 0) return null;

  return { x: minX, y: minY, width, height };
}

function monitorContainsPhysicalPoint(
  monitor: MonitorPhysicalSnapshot,
  point: PetPoint,
): boolean {
  return (
    point.x >= monitor.x &&
    point.x <= monitor.x + monitor.width &&
    point.y >= monitor.y &&
    point.y <= monitor.y + monitor.height
  );
}

function distanceToPhysicalMonitor(
  monitor: MonitorPhysicalSnapshot,
  point: PetPoint,
): number {
  const nearestX = clamp(point.x, monitor.x, monitor.x + monitor.width);
  const nearestY = clamp(point.y, monitor.y, monitor.y + monitor.height);
  return Math.hypot(point.x - nearestX, point.y - nearestY);
}

function selectPhysicalMonitorForPoint(
  point: PetPoint,
  monitors: MonitorPhysicalSnapshot[],
): MonitorPhysicalSnapshot | null {
  const usableMonitors = monitors.filter(isUsablePhysicalMonitor);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || usableMonitors.length === 0) {
    return null;
  }

  return (
    usableMonitors.find((monitor) => monitorContainsPhysicalPoint(monitor, point)) ??
    [...usableMonitors].sort(
      (a, b) => distanceToPhysicalMonitor(a, point) - distanceToPhysicalMonitor(b, point),
    )[0] ??
    null
  );
}

export function resolvePetInitialPlacementPoint(
  input: PetInitialPlacementInput,
): PetPoint | null {
  const selectedMonitor = selectPhysicalMonitorForPoint(input.cursor, input.monitors);
  if (!selectedMonitor) return null;

  const viewports = computeMonitorViewportsWithinBounds(
    input.monitors,
    input.bounds,
    input.overlaySize,
  );
  const viewport = matchNextMonitorViewport(selectedMonitor, viewports);
  if (!viewport) return null;

  return {
    x: viewport.x + viewport.w / 2,
    y: viewport.y + viewport.h / 2,
  };
}

export function resolvePetMonitorRelativeAnchorForPoint(input: {
  point: PetPoint;
  bounds: OverlayPhysicalBounds;
  monitors: MonitorPhysicalSnapshot[];
  overlaySize: CssSize;
}): PetMonitorRelativeAnchor | null {
  const viewports = computeMonitorViewportsWithinBounds(
    input.monitors,
    input.bounds,
    input.overlaySize,
  );
  const viewport = selectMonitorViewportForPoint(input.point, viewports);
  if (!viewport || viewport.w <= 0 || viewport.h <= 0) return null;

  return {
    monitor: viewport.monitor,
    relativeAnchor: {
      x: clamp((input.point.x - viewport.x) / viewport.w, 0, 1),
      y: clamp((input.point.y - viewport.y) / viewport.h, 0, 1),
    },
  };
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

function isUsablePhysicalBounds(bounds: OverlayPhysicalBounds): boolean {
  return (
    Number.isFinite(bounds.x) &&
    Number.isFinite(bounds.y) &&
    Number.isFinite(bounds.width) &&
    Number.isFinite(bounds.height) &&
    bounds.width > 0 &&
    bounds.height > 0
  );
}

function isUsablePhysicalMonitor(monitor: MonitorPhysicalSnapshot): boolean {
  return (
    Number.isFinite(monitor.x) &&
    Number.isFinite(monitor.y) &&
    Number.isFinite(monitor.width) &&
    Number.isFinite(monitor.height) &&
    monitor.width > 0 &&
    monitor.height > 0
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function containsPoint(viewport: PetViewport, point: PetPoint): boolean {
  return (
    point.x >= viewport.x &&
    point.x <= viewport.x + viewport.w &&
    point.y >= viewport.y &&
    point.y <= viewport.y + viewport.h
  );
}

function distanceToViewportPoint(viewport: PetViewport, point: PetPoint): number {
  const nearestX = clamp(point.x, viewport.x, viewport.x + viewport.w);
  const nearestY = clamp(point.y, viewport.y, viewport.y + viewport.h);
  return Math.hypot(point.x - nearestX, point.y - nearestY);
}

function selectMonitorViewportForPoint(
  point: PetPoint,
  viewports: MonitorViewportSnapshot[],
): MonitorViewportSnapshot | null {
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || viewports.length === 0) {
    return null;
  }

  const containing = viewports.filter((viewport) => containsPoint(viewport, point));
  if (containing.length > 0) {
    return containing.sort(
      (a, b) =>
        Math.hypot(point.x - (a.x + a.w / 2), point.y - (a.y + a.h / 2)) -
        Math.hypot(point.x - (b.x + b.w / 2), point.y - (b.y + b.h / 2)),
    )[0];
  }

  return [...viewports].sort(
    (a, b) => distanceToViewportPoint(a, point) - distanceToViewportPoint(b, point),
  )[0];
}

function monitorMatchScore(
  previous: MonitorPhysicalSnapshot,
  next: MonitorPhysicalSnapshot,
): number {
  const widthBase = Math.max(previous.width, next.width, 1);
  const heightBase = Math.max(previous.height, next.height, 1);
  const sizeScore =
    Math.abs(previous.width - next.width) / widthBase +
    Math.abs(previous.height - next.height) / heightBase;
  const scaleScore =
    Math.abs((previous.scaleMilli ?? 0) - (next.scaleMilli ?? 0)) / 1000;
  const positionScore =
    (Math.abs(previous.x - next.x) + Math.abs(previous.y - next.y)) / 100000;
  return sizeScore * 1000 + scaleScore * 100 + positionScore;
}

function matchNextMonitorViewport(
  previous: MonitorPhysicalSnapshot,
  nextViewports: MonitorViewportSnapshot[],
): MonitorViewportSnapshot | null {
  if (nextViewports.length === 0) return null;
  if (nextViewports.length === 1) return nextViewports[0];

  return [...nextViewports].sort(
    (a, b) =>
      monitorMatchScore(previous, a.monitor) - monitorMatchScore(previous, b.monitor) ||
      a.index - b.index,
  )[0];
}

export interface PetPointMonitorRebaseInput {
  point: PetPoint;
  previousBounds: OverlayPhysicalBounds;
  nextBounds: OverlayPhysicalBounds;
  previousMonitors: MonitorPhysicalSnapshot[];
  nextMonitors: MonitorPhysicalSnapshot[];
  previousOverlaySize: CssSize;
  nextOverlaySize: CssSize;
}

export function rebasePetPointByMonitorAnchorWithDiagnostics(
  input: PetPointMonitorRebaseInput,
): PetPointMonitorRebaseResult {
  const previousViewports = computeMonitorViewportsWithinBounds(
    input.previousMonitors,
    input.previousBounds,
    input.previousOverlaySize,
  );
  const nextViewports = computeMonitorViewportsWithinBounds(
    input.nextMonitors,
    input.nextBounds,
    input.nextOverlaySize,
  );
  const previousViewport = selectMonitorViewportForPoint(input.point, previousViewports);
  if (!previousViewport || previousViewport.w <= 0 || previousViewport.h <= 0) {
    return {
      point: null,
      diagnostics: {
        previousViewports,
        nextViewports,
        previousViewport,
        nextViewport: null,
        relativeAnchor: null,
      },
    };
  }

  const nextViewport = matchNextMonitorViewport(previousViewport.monitor, nextViewports);
  if (!nextViewport) {
    return {
      point: null,
      diagnostics: {
        previousViewports,
        nextViewports,
        previousViewport,
        nextViewport,
        relativeAnchor: null,
      },
    };
  }

  const relativeX = clamp(
    (input.point.x - previousViewport.x) / previousViewport.w,
    0,
    1,
  );
  const relativeY = clamp(
    (input.point.y - previousViewport.y) / previousViewport.h,
    0,
    1,
  );
  const point = {
    x: clamp(
      nextViewport.x + nextViewport.w * relativeX,
      nextViewport.x,
      nextViewport.x + nextViewport.w,
    ),
    y: clamp(
      nextViewport.y + nextViewport.h * relativeY,
      nextViewport.y,
      nextViewport.y + nextViewport.h,
    ),
  };
  return {
    point,
    diagnostics: {
      previousViewports,
      nextViewports,
      previousViewport,
      nextViewport,
      relativeAnchor: { x: relativeX, y: relativeY },
    },
  };
}

export function rebasePetPointByMonitorAnchor(
  input: PetPointMonitorRebaseInput,
): PetPoint | null {
  return rebasePetPointByMonitorAnchorWithDiagnostics(input).point;
}

export function resolvePetPointFromMonitorRelativeAnchor(input: {
  anchor: PetMonitorRelativeAnchor;
  bounds: OverlayPhysicalBounds;
  monitors: MonitorPhysicalSnapshot[];
  overlaySize: CssSize;
}): PetPoint | null {
  const viewports = computeMonitorViewportsWithinBounds(
    input.monitors,
    input.bounds,
    input.overlaySize,
  );
  const viewport = matchNextMonitorViewport(input.anchor.monitor, viewports);
  if (!viewport) return null;

  return {
    x: clamp(
      viewport.x + viewport.w * clamp(input.anchor.relativeAnchor.x, 0, 1),
      viewport.x,
      viewport.x + viewport.w,
    ),
    y: clamp(
      viewport.y + viewport.h * clamp(input.anchor.relativeAnchor.y, 0, 1),
      viewport.y,
      viewport.y + viewport.h,
    ),
  };
}

export async function loadPetMonitorViewports(
  overlayViewport: PetViewport = getPetOverlayViewport(),
): Promise<PetViewport[]> {
  if (!isTauri()) return [];
  const monitors: Monitor[] = await availableMonitors();
  return computeMonitorViewports(monitors, overlayViewport);
}

function monitorToPhysicalSnapshot(monitor: Monitor): MonitorPhysicalSnapshot {
  return {
    x: monitor.position.x,
    y: monitor.position.y,
    width: monitor.size.width,
    height: monitor.size.height,
    scaleMilli:
      typeof monitor.scaleFactor === "number"
        ? Math.round(monitor.scaleFactor * 1000)
        : null,
  };
}

export async function loadPetDisplayPhysicalSnapshot(): Promise<PetDisplayPhysicalSnapshot | null> {
  if (!isTauri()) return null;
  const monitors = (await availableMonitors()).map(monitorToPhysicalSnapshot);
  const bounds = computeOverlayPhysicalBounds(monitors);
  return bounds ? { bounds, monitors } : null;
}

export async function loadPetUiPersistence(): Promise<PetUiPersistence> {
  if (!isTauri()) return {};
  return (await invoke("get_pet_ui_persistence")) as PetUiPersistence;
}

export async function savePetMonitorAnchor(
  anchor: PetMonitorRelativeAnchor | null,
): Promise<void> {
  if (!isTauri()) return;
  await invoke("set_pet_monitor_anchor", { payload: { anchor } });
}

export async function loadPetInitialPlacementPoint(
  overlayViewport: PetViewport = getPetOverlayViewport(),
): Promise<PetPoint | null> {
  if (!isTauri() || !isFiniteViewport(overlayViewport)) return null;

  const [snapshot, persisted] = await Promise.all([
    loadPetDisplayPhysicalSnapshot(),
    loadPetUiPersistence(),
  ]);
  if (!snapshot) return null;
  if (persisted.anchor) {
    const restoredPoint = resolvePetPointFromMonitorRelativeAnchor({
      anchor: persisted.anchor,
      bounds: snapshot.bounds,
      monitors: snapshot.monitors,
      overlaySize: { w: overlayViewport.w, h: overlayViewport.h },
    });
    if (restoredPoint) return restoredPoint;
  }

  // Cursor placement is only a first-run fallback. A transient cursor API failure must never
  // prevent an already-persisted anchor from being restored.
  const cursor = await cursorPosition();
  return resolvePetInitialPlacementPoint({
    cursor: { x: cursor.x, y: cursor.y },
    bounds: snapshot.bounds,
    monitors: snapshot.monitors,
    overlaySize: { w: overlayViewport.w, h: overlayViewport.h },
  });
}
