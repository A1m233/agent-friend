import type { PetFacing } from "@/pet/avatarRuntime/facing";
import type { PixelRegion } from "@/pet/petAlphaHitTest";
import type { PixelPoint } from "./spine38Placement";

export const FACING_SWITCH_DISTANCE_PX = 8;
export const HORIZONTAL_INTENT_RATIO = 0.5;

export function facingScaleX(facing: PetFacing): 1 | -1 {
  return facing === "left" ? -1 : 1;
}

export class Spine38DragFacingDetector {
  private currentFacing: PetFacing;
  private lastPoint: PixelPoint | null = null;
  private candidateFacing: PetFacing | null = null;
  private candidateDistance = 0;

  constructor(initialFacing: PetFacing) {
    this.currentFacing = initialFacing;
  }

  begin(point: PixelPoint, facing: PetFacing = this.currentFacing): void {
    this.currentFacing = facing;
    this.lastPoint = point;
    this.clearCandidate();
  }

  move(point: PixelPoint): PetFacing | null {
    const previousPoint = this.lastPoint;
    this.lastPoint = point;
    if (!previousPoint) return null;

    const stepX = point.x - previousPoint.x;
    const stepY = point.y - previousPoint.y;
    if (
      stepX === 0 ||
      Math.abs(stepX) < Math.abs(stepY) * HORIZONTAL_INTENT_RATIO
    ) {
      return null;
    }

    const nextCandidate: PetFacing = stepX < 0 ? "left" : "right";
    if (nextCandidate === this.currentFacing) {
      this.clearCandidate();
      return null;
    }

    if (this.candidateFacing !== nextCandidate) {
      this.candidateFacing = nextCandidate;
      this.candidateDistance = Math.abs(stepX);
    } else {
      this.candidateDistance += Math.abs(stepX);
    }

    if (this.candidateDistance < FACING_SWITCH_DISTANCE_PX) return null;

    this.currentFacing = nextCandidate;
    this.clearCandidate();
    return nextCandidate;
  }

  end(): void {
    this.lastPoint = null;
    this.clearCandidate();
  }

  private clearCandidate(): void {
    this.candidateFacing = null;
    this.candidateDistance = 0;
  }
}

export function spineLocalRegionToSlotRegion(
  spinePosition: PixelPoint,
  region: PixelRegion,
  facing: PetFacing,
): PixelRegion {
  const scaleX = facingScaleX(facing);
  const x1 = (spinePosition.x + region.x) * scaleX;
  const x2 = (spinePosition.x + region.x + region.w) * scaleX;
  return {
    x: Math.min(x1, x2),
    y: spinePosition.y + region.y,
    w: Math.abs(x2 - x1),
    h: region.h,
  };
}

export function slotRegionToScreenRegion(
  slotPosition: PixelPoint,
  slotScale: PixelPoint,
  region: PixelRegion,
): PixelRegion {
  const x1 = slotPosition.x + region.x * slotScale.x;
  const x2 = slotPosition.x + (region.x + region.w) * slotScale.x;
  const y1 = slotPosition.y + region.y * slotScale.y;
  const y2 = slotPosition.y + (region.y + region.h) * slotScale.y;
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.abs(x2 - x1),
    h: Math.abs(y2 - y1),
  };
}

export function screenRegionToSpineLocalRegion(
  slotPosition: PixelPoint,
  slotScale: PixelPoint,
  spinePosition: PixelPoint,
  region: PixelRegion,
  facing: PetFacing,
): PixelRegion {
  const scaleX = slotScale.x || 1;
  const scaleY = slotScale.y || 1;
  const screenX1 = (region.x - slotPosition.x) / scaleX;
  const screenX2 = (region.x + region.w - slotPosition.x) / scaleX;
  const localX1 = screenX1 * facingScaleX(facing) - spinePosition.x;
  const localX2 = screenX2 * facingScaleX(facing) - spinePosition.x;
  const localY1 = (region.y - slotPosition.y) / scaleY - spinePosition.y;
  const localY2 =
    (region.y + region.h - slotPosition.y) / scaleY - spinePosition.y;
  return {
    x: Math.min(localX1, localX2),
    y: Math.min(localY1, localY2),
    w: Math.abs(localX2 - localX1),
    h: Math.abs(localY2 - localY1),
  };
}

export function spineLocalPointToScreenPoint(
  slotPosition: PixelPoint,
  slotScale: PixelPoint,
  spinePosition: PixelPoint,
  point: PixelPoint,
  facing: PetFacing,
): PixelPoint {
  return {
    x:
      slotPosition.x +
      (spinePosition.x + point.x) * facingScaleX(facing) * slotScale.x,
    y: slotPosition.y + (spinePosition.y + point.y) * slotScale.y,
  };
}

export function screenPointToSpineLocalPoint(
  slotPosition: PixelPoint,
  slotScale: PixelPoint,
  spinePosition: PixelPoint,
  point: PixelPoint,
  facing: PetFacing,
): PixelPoint {
  return {
    x:
      ((point.x - slotPosition.x) / (slotScale.x || 1)) *
        facingScaleX(facing) -
      spinePosition.x,
    y: (point.y - slotPosition.y) / (slotScale.y || 1) - spinePosition.y,
  };
}

export function orientRegionOffset(
  offset: PixelRegion,
  baseWidth: number,
  facing: PetFacing,
): PixelRegion {
  return facing === "left"
    ? { ...offset, x: baseWidth - offset.x - offset.w }
    : offset;
}

export function orientPointOffset(
  offset: PixelPoint,
  baseWidth: number,
  facing: PetFacing,
): PixelPoint {
  return facing === "left" ? { ...offset, x: baseWidth - offset.x } : offset;
}
