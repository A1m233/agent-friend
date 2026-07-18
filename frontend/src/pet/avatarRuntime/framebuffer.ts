import type { PixelRegion } from "@/pet/petAlphaHitTest";

export interface VisibleRegionSample {
  bounds: PixelRegion;
  centroid: { x: number; y: number };
}

export interface AdaptiveVisibleRegionOptions {
  threshold?: number;
  paddingRatio?: number;
  minPaddingPx?: number;
  maxPasses?: number;
  edgeTolerancePx?: number;
}

interface CanvasCarrier {
  canvas?: unknown;
  view?: unknown;
  renderer?: {
    canvas?: unknown;
    view?: unknown;
    resolution?: unknown;
  };
}

export function getPixiCanvas(app: unknown): HTMLCanvasElement | null {
  const carrier = app as CanvasCarrier | null;
  if (!carrier) return null;

  const canvas = carrier.canvas ?? carrier.view ?? carrier.renderer?.canvas ?? carrier.renderer?.view;
  return canvas instanceof HTMLCanvasElement ? canvas : null;
}

export function getPixiResolution(app: unknown): number {
  const carrier = app as CanvasCarrier | null;
  const resolution = carrier?.renderer?.resolution;
  return typeof resolution === "number" && Number.isFinite(resolution) && resolution > 0
    ? resolution
    : 1;
}

export function alphaHitTestFromApp(
  app: unknown,
  cursor: { x: number; y: number },
  threshold = 30,
): boolean {
  const canvas = getPixiCanvas(app);
  if (!canvas) return false;

  const gl = canvas.getContext("webgl2") as WebGL2RenderingContext | null;
  if (!gl) return false;

  const dpr = getPixiResolution(app);
  const cx = Math.round(cursor.x * dpr);
  const cy = Math.round(canvas.height - cursor.y * dpr);
  if (cx < 0 || cy < 0 || cx >= canvas.width || cy >= canvas.height) return false;

  const buf = new Uint8Array(4);
  try {
    gl.readPixels(cx, cy, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  } catch {
    return false;
  }
  return buf[3] > threshold;
}

export function findVisibleBoundsFromApp(
  app: unknown,
  region: PixelRegion,
  threshold = 30,
): PixelRegion | null {
  return findVisibleRegionFromApp(app, region, threshold)?.bounds ?? null;
}

export function findVisibleRegionFromApp(
  app: unknown,
  region: PixelRegion,
  threshold = 30,
): VisibleRegionSample | null {
  const canvas = getPixiCanvas(app);
  if (!canvas) return null;

  const gl = canvas.getContext("webgl2") as WebGL2RenderingContext | null;
  if (!gl) return null;

  const dpr = getPixiResolution(app);
  const px0 = Math.max(0, Math.floor(region.x * dpr));
  const py0 = Math.max(0, Math.floor(region.y * dpr));
  const px1 = Math.min(canvas.width, Math.ceil((region.x + region.w) * dpr));
  const py1 = Math.min(canvas.height, Math.ceil((region.y + region.h) * dpr));
  const w = px1 - px0;
  const h = py1 - py0;
  if (w <= 0 || h <= 0) return null;

  const buf = new Uint8Array(w * h * 4);
  try {
    gl.readPixels(px0, canvas.height - py1, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  } catch {
    return null;
  }

  let minX = w;
  let minY = h;
  let maxX = -1;
  let maxY = -1;
  let weightedX = 0;
  let weightedY = 0;
  let totalWeight = 0;
  for (let row = 0; row < h; row++) {
    const canvasRow = h - 1 - row;
    const rowOffset = row * w * 4;
    for (let col = 0; col < w; col++) {
      const alpha = buf[rowOffset + col * 4 + 3];
      if (alpha > threshold) {
        if (col < minX) minX = col;
        if (canvasRow < minY) minY = canvasRow;
        if (col > maxX) maxX = col;
        if (canvasRow > maxY) maxY = canvasRow;
        weightedX += (px0 + col + 0.5) * alpha;
        weightedY += (py0 + canvasRow + 0.5) * alpha;
        totalWeight += alpha;
      }
    }
  }
  if (maxX < 0) return null;

  return {
    bounds: {
      x: (px0 + minX) / dpr,
      y: (py0 + minY) / dpr,
      w: (maxX - minX + 1) / dpr,
      h: (maxY - minY + 1) / dpr,
    },
    centroid: {
      x: weightedX / totalWeight / dpr,
      y: weightedY / totalWeight / dpr,
    },
  };
}

function expandRegionWithinCanvas(
  app: unknown,
  region: PixelRegion,
  sides: { left: boolean; top: boolean; right: boolean; bottom: boolean },
  paddingX: number,
  paddingY: number,
): PixelRegion {
  const canvas = getPixiCanvas(app);
  const dpr = getPixiResolution(app);
  const canvasWidth = canvas ? canvas.width / dpr : region.x + region.w;
  const canvasHeight = canvas ? canvas.height / dpr : region.y + region.h;
  const x0 = Math.max(0, region.x - (sides.left ? paddingX : 0));
  const y0 = Math.max(0, region.y - (sides.top ? paddingY : 0));
  const x1 = Math.min(
    canvasWidth,
    region.x + region.w + (sides.right ? paddingX : 0),
  );
  const y1 = Math.min(
    canvasHeight,
    region.y + region.h + (sides.bottom ? paddingY : 0),
  );
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/**
 * Treats geometry bounds as a scan hint, never as a hard visual boundary.
 *
 * Spine attachment AABBs can omit rendered pixels (for example Amiya's ears), so the first
 * pass adds a guard band on every side. If visible pixels still touch a scan edge, that edge
 * expands again until the content is enclosed or the pass limit/canvas edge is reached.
 */
export function findAdaptiveVisibleRegionFromApp(
  app: unknown,
  initialRegion: PixelRegion,
  options: AdaptiveVisibleRegionOptions = {},
): VisibleRegionSample | null {
  const threshold = options.threshold ?? 30;
  const paddingRatio = options.paddingRatio ?? 1;
  const minPaddingPx = options.minPaddingPx ?? 16;
  const maxPasses = Math.max(1, Math.floor(options.maxPasses ?? 4));
  const edgeTolerancePx = options.edgeTolerancePx ?? 1;
  const paddingX = Math.max(minPaddingPx, initialRegion.w * paddingRatio);
  const paddingY = Math.max(minPaddingPx, initialRegion.h * paddingRatio);
  const allSides = { left: true, top: true, right: true, bottom: true };
  let scanRegion = expandRegionWithinCanvas(
    app,
    initialRegion,
    allSides,
    paddingX,
    paddingY,
  );
  let latest: VisibleRegionSample | null = null;

  for (let pass = 0; pass < maxPasses; pass += 1) {
    latest = findVisibleRegionFromApp(app, scanRegion, threshold);
    if (!latest) return null;

    const bounds = latest.bounds;
    const touches = {
      left: bounds.x <= scanRegion.x + edgeTolerancePx,
      top: bounds.y <= scanRegion.y + edgeTolerancePx,
      right:
        bounds.x + bounds.w >= scanRegion.x + scanRegion.w - edgeTolerancePx,
      bottom:
        bounds.y + bounds.h >= scanRegion.y + scanRegion.h - edgeTolerancePx,
    };
    if (!touches.left && !touches.top && !touches.right && !touches.bottom) {
      return latest;
    }

    const expanded = expandRegionWithinCanvas(
      app,
      scanRegion,
      touches,
      paddingX,
      paddingY,
    );
    if (
      expanded.x === scanRegion.x &&
      expanded.y === scanRegion.y &&
      expanded.w === scanRegion.w &&
      expanded.h === scanRegion.h
    ) {
      return latest;
    }
    scanRegion = expanded;
  }

  return latest;
}
