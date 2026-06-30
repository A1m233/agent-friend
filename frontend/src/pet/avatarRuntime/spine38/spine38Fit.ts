import type * as PIXI7 from "pixi7";
import type { Spine as Spine38 } from "pixi-spine";
import type { AvatarFitConfig } from "@/avatar-models";
import type { PixelRegion } from "@/pet/petAlphaHitTest";

export interface Spine38StageViewport {
  x: number;
  y: number;
  w: number;
  h: number;
}

const DEFAULT_FIT = {
  fitWidthPx: null,
  fitHeightPx: null,
  fitWidthRatio: 0.62,
  fitHeightRatio: 0.68,
  slotYRatio: 0.86,
  topPaddingRatio: 0.16,
};

function resolveFitBoxSize(stageSize: number, configuredSize?: number | null): number {
  if (
    typeof configuredSize !== "number" ||
    !Number.isFinite(configuredSize) ||
    configuredSize <= 0
  ) {
    return stageSize;
  }
  return Math.min(stageSize, configuredSize);
}

export function boundsToRegion(bounds: unknown): PixelRegion | null {
  if (!bounds || typeof bounds !== "object") return null;
  const item = bounds as {
    x?: number;
    y?: number;
    minX?: number;
    minY?: number;
    width?: number;
    height?: number;
  };
  const x = typeof item.minX === "number" ? item.minX : item.x;
  const y = typeof item.minY === "number" ? item.minY : item.y;
  const w = item.width;
  const h = item.height;
  if (
    typeof x !== "number" ||
    typeof y !== "number" ||
    typeof w !== "number" ||
    typeof h !== "number" ||
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    !Number.isFinite(w) ||
    !Number.isFinite(h)
  ) {
    return null;
  }
  return { x, y, w, h };
}

export function fitSpine38ToStage(
  app: PIXI7.Application<HTMLCanvasElement>,
  slot: PIXI7.Container,
  spine: Spine38,
  fit?: AvatarFitConfig,
  layoutBounds?: PixelRegion | null,
  viewport?: Spine38StageViewport | null,
): void {
  const fitWidthPx = fit?.fitWidthPx ?? DEFAULT_FIT.fitWidthPx;
  const fitHeightPx = fit?.fitHeightPx ?? DEFAULT_FIT.fitHeightPx;
  const fitWidthRatio = fit?.fitWidthRatio ?? DEFAULT_FIT.fitWidthRatio;
  const fitHeightRatio = fit?.fitHeightRatio ?? DEFAULT_FIT.fitHeightRatio;
  const slotYRatio = fit?.slotYRatio ?? DEFAULT_FIT.slotYRatio;
  const topPaddingRatio = fit?.topPaddingRatio ?? DEFAULT_FIT.topPaddingRatio;
  const stageViewport =
    viewport &&
    Number.isFinite(viewport.x) &&
    Number.isFinite(viewport.y) &&
    viewport.w > 0 &&
    viewport.h > 0
      ? viewport
      : { x: 0, y: 0, w: app.screen.width, h: app.screen.height };
  const fitBoxWidth = resolveFitBoxSize(stageViewport.w, fitWidthPx);
  const fitBoxHeight = resolveFitBoxSize(stageViewport.h, fitHeightPx);
  const maxHeightRatio = Math.max(0.01, slotYRatio - topPaddingRatio);
  const effectiveFitHeightRatio = Math.min(fitHeightRatio, maxHeightRatio);

  slot.scale.set(1);
  slot.position.set(
    stageViewport.x + stageViewport.w / 2,
    stageViewport.y + stageViewport.h * slotYRatio,
  );
  spine.position.set(0, 0);
  spine.update(0);

  const localBounds = layoutBounds ?? boundsToRegion(spine.getLocalBounds());
  if (!localBounds || localBounds.w <= 0 || localBounds.h <= 0) return;

  const fitScale = Math.min(
    (fitBoxWidth * fitWidthRatio) / localBounds.w,
    (fitBoxHeight * effectiveFitHeightRatio) / localBounds.h,
  );
  const safeScale = Number.isFinite(fitScale) && fitScale > 0 ? fitScale : 1;
  slot.scale.set(safeScale);
  spine.position.set(
    -localBounds.x - localBounds.w / 2,
    -localBounds.y - localBounds.h,
  );
}
