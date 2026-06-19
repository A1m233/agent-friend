import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

/**
 * 024 · 拖拽视觉反馈。
 *
 * 拖拽中按拖动方向给 ParamAngleZ / ParamBodyAngleZ 叠加摇晃，
 * 并抬眉、惊讶嘴形。mouse up 后 300ms 线性回归中立。
 */
const RELEASE_DURATION_MS = 300;
const ANGLE_Z_PEAK = 25;
const BODY_ANGLE_Z_PEAK = 15;
const BROW_FORM_PEAK = 1.0;
const BROW_Y_PEAK = 0.8;
const MOUTH_FORM_PEAK = -0.8;

export class DragReactionSource implements ParamSource {
  private isDragging = false;
  private dragVelX = 0;
  private dragVelY = 0;
  private releaseStartTs: number | null = null;

  setDragging(v: boolean, vx = 0, vy = 0): void {
    if (v) {
      this.isDragging = true;
      this.dragVelX = clamp(vx, -1, 1);
      this.dragVelY = clamp(vy, -1, 1);
      this.releaseStartTs = null;
      if (import.meta.env.DEV) {
        console.info("[drag] start", { vx: this.dragVelX, vy: this.dragVelY });
      }
    } else if (this.isDragging) {
      this.isDragging = false;
      this.releaseStartTs = performance.now();
      if (import.meta.env.DEV) {
        console.info("[drag] end");
      }
    }
  }

  /** 拖动中由 slot 喂"瞬时方向"（normalized）。 */
  updateDragDirection(vx: number, vy: number): void {
    if (!this.isDragging) return;
    this.dragVelX = clamp(vx, -1, 1);
    this.dragVelY = clamp(vy, -1, 1);
  }

  get active(): boolean {
    if (this.isDragging) return true;
    if (this.releaseStartTs === null) return false;
    return performance.now() - this.releaseStartTs < RELEASE_DURATION_MS;
  }

  apply(sprite: Live2DSprite): void {
    let envelope = 1;
    if (!this.isDragging && this.releaseStartTs !== null) {
      const elapsed = performance.now() - this.releaseStartTs;
      envelope = Math.max(0, 1 - elapsed / RELEASE_DURATION_MS);
    }
    sprite.setParameterValueById("ParamAngleZ", ANGLE_Z_PEAK * this.dragVelX * envelope);
    sprite.setParameterValueById("ParamBodyAngleZ", BODY_ANGLE_Z_PEAK * this.dragVelX * envelope);
    sprite.setParameterValueById("ParamBrowLForm", BROW_FORM_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowRForm", BROW_FORM_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowLY", BROW_Y_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowRY", BROW_Y_PEAK * envelope);
    sprite.setParameterValueById("ParamMouthForm", MOUTH_FORM_PEAK * envelope);
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
