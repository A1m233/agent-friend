interface PixiRendererLike {
  resolution?: number;
  resize?: (width: number, height: number) => void;
}

interface PixiApplicationLike {
  renderer?: PixiRendererLike;
  screen?: {
    width?: number;
    height?: number;
  };
  resize?: () => void;
  queueResize?: () => void;
}

function normalizeDpr(dpr: number): number {
  return Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
}

function readPositiveSize(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

export function syncPixiRendererDpr(app: unknown, dpr: number): boolean {
  const candidate = app as PixiApplicationLike | null;
  const renderer = candidate?.renderer;
  if (!renderer) return false;

  const nextDpr = normalizeDpr(dpr);
  if (
    typeof renderer.resolution === "number" &&
    Math.abs(renderer.resolution - nextDpr) < 0.001
  ) {
    return false;
  }

  renderer.resolution = nextDpr;

  const width = readPositiveSize(candidate?.screen?.width);
  const height = readPositiveSize(candidate?.screen?.height);
  if (width && height && typeof renderer.resize === "function") {
    renderer.resize(width, height);
  } else if (typeof candidate?.resize === "function") {
    candidate.resize();
  } else if (typeof candidate?.queueResize === "function") {
    candidate.queueResize();
  }
  return true;
}
