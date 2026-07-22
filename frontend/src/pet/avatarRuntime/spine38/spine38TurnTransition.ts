import type { PetFacing } from "@/pet/avatarRuntime/facing";
import { facingScaleX } from "./spine38Facing";

export const SPINE38_TURN_DURATION_MS = 160;

export interface Spine38TurnFrame {
  scaleX: number;
  settled: boolean;
  completedFacing?: PetFacing;
}

function easeInOut(value: number): number {
  return value < 0.5 ? 2 * value * value : 1 - (-2 * value + 2) ** 2 / 2;
}

export class Spine38TurnTransition {
  private startScaleX: number;
  private currentScaleX: number;
  private elapsedMs = 0;
  private active = false;

  targetFacing: PetFacing;

  constructor(initialFacing: PetFacing) {
    this.targetFacing = initialFacing;
    this.startScaleX = facingScaleX(initialFacing);
    this.currentScaleX = this.startScaleX;
  }

  get scaleX(): number {
    return this.currentScaleX;
  }

  get isAnimating(): boolean {
    return this.active;
  }

  snap(facing: PetFacing): Spine38TurnFrame {
    this.targetFacing = facing;
    this.currentScaleX = facingScaleX(facing);
    this.startScaleX = this.currentScaleX;
    this.elapsedMs = 0;
    this.active = false;
    return { scaleX: this.currentScaleX, settled: true };
  }

  turnTo(facing: PetFacing): boolean {
    if (facing === this.targetFacing) return false;
    this.targetFacing = facing;
    this.startScaleX = this.currentScaleX;
    this.elapsedMs = 0;
    this.active = true;
    return true;
  }

  update(deltaMs: number): Spine38TurnFrame {
    if (!this.active) return { scaleX: this.currentScaleX, settled: true };

    this.elapsedMs = Math.min(
      SPINE38_TURN_DURATION_MS,
      this.elapsedMs + Math.max(0, deltaMs),
    );
    const progress = this.elapsedMs / SPINE38_TURN_DURATION_MS;
    const targetScaleX = facingScaleX(this.targetFacing);
    this.currentScaleX =
      this.startScaleX + (targetScaleX - this.startScaleX) * easeInOut(progress);

    if (progress < 1) {
      return { scaleX: this.currentScaleX, settled: false };
    }

    this.currentScaleX = targetScaleX;
    this.active = false;
    return {
      scaleX: this.currentScaleX,
      settled: true,
      completedFacing: this.targetFacing,
    };
  }
}
