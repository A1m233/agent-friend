import type { PixelRegion } from "@/pet/petAlphaHitTest";

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
      }
    }
  }
  if (maxX < 0) return null;

  return {
    x: (px0 + minX) / dpr,
    y: (py0 + minY) / dpr,
    w: (maxX - minX + 1) / dpr,
    h: (maxY - minY + 1) / dpr,
  };
}
