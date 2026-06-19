import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

/**
 * 024 · 视线 / 头跟随光标。
 *
 * 输入 Rust pet://cursor 通道的全屏 CSS px 坐标，经 EMA 低通滤波后驱动
 * ParamAngleX/Y/Z + ParamEyeBallX/Y。
 *
 * 光标距桌宠超过阈值时 target 归零，EMA 让角度平滑回中立。
 */
const DISTANCE_THRESHOLD_FRAC = 0.5; // 屏幕宽/高中较大者的 1/2 起，超出归零
const ANGLE_X_MAX_DEG = 45;
const ANGLE_Y_MAX_DEG = 30;
const ANGLE_Z_MAX_DEG = 15;
const EYEBALL_MAX = 1;
const EMA_TAU_MS = 200;

export interface SpriteScreen {
  x: number;
  y: number;
  w: number;
  h: number;
}

export class GazeSource implements ParamSource {
  private isActive = true;
  private current = { angleX: 0, angleY: 0, angleZ: 0, eyeBallX: 0, eyeBallY: 0 };
  private target = { angleX: 0, angleY: 0, angleZ: 0, eyeBallX: 0, eyeBallY: 0 };
  private lastApplyTs = 0;

  setActive(v: boolean): void {
    this.isActive = v;
  }

  get active(): boolean {
    return this.isActive;
  }

  /** 由 pet://cursor 副订阅 ~60Hz 喂；输入是 CSS px（viewport 内）。 */
  updateCursor(cx: number, cy: number, sprite: SpriteScreen): void {
    const spriteCx = sprite.x + sprite.w / 2;
    const spriteCy = sprite.y + sprite.h / 2;
    const dx = cx - spriteCx;
    const dy = cy - spriteCy;
    const dist = Math.hypot(dx, dy);

    const viewportW = window.innerWidth;
    const viewportH = window.innerHeight;
    const threshold = Math.max(viewportW, viewportH) * DISTANCE_THRESHOLD_FRAC;
    const factor = dist > threshold ? 0 : 1;

    // 用 threshold 作为映射基线：在 threshold 边缘就达到最大角度，让近处跟随更明显。
    const normW = Math.max(1, threshold);
    const normH = Math.max(1, threshold);

    // 光标在左 → 头左转 / 眼球看左；光标在右 → 头右转 / 眼球看右。
    // Live2D 标准约定：ParamAngleX / ParamEyeBallX 正方向为“朝右”。
    this.target.angleX = clamp((dx / normW) * ANGLE_X_MAX_DEG * factor, -ANGLE_X_MAX_DEG, ANGLE_X_MAX_DEG);
    // 屏幕 Y 朝下 / Live2D angleY 正向头抬起 → 取反
    this.target.angleY = clamp((-dy / normH) * ANGLE_Y_MAX_DEG * factor, -ANGLE_Y_MAX_DEG, ANGLE_Y_MAX_DEG);
    this.target.angleZ = clamp((dx / normW) * ANGLE_Z_MAX_DEG * factor, -ANGLE_Z_MAX_DEG, ANGLE_Z_MAX_DEG);
    this.target.eyeBallX = clamp((dx / normW) * EYEBALL_MAX * factor, -EYEBALL_MAX, EYEBALL_MAX);
    this.target.eyeBallY = clamp((-dy / normH) * EYEBALL_MAX * factor, -EYEBALL_MAX, EYEBALL_MAX);
  }

  apply(sprite: Live2DSprite): void {
    const now = performance.now();
    const dt = this.lastApplyTs === 0 ? 16 : now - this.lastApplyTs;
    this.lastApplyTs = now;
    const alpha = Math.min(1, dt / (EMA_TAU_MS + dt));

    this.current.angleX += alpha * (this.target.angleX - this.current.angleX);
    this.current.angleY += alpha * (this.target.angleY - this.current.angleY);
    this.current.angleZ += alpha * (this.target.angleZ - this.current.angleZ);
    this.current.eyeBallX += alpha * (this.target.eyeBallX - this.current.eyeBallX);
    this.current.eyeBallY += alpha * (this.target.eyeBallY - this.current.eyeBallY);

    sprite.setParameterValueById("ParamAngleX", this.current.angleX);
    sprite.setParameterValueById("ParamAngleY", this.current.angleY);
    sprite.setParameterValueById("ParamAngleZ", this.current.angleZ);
    sprite.setParameterValueById("ParamEyeBallX", this.current.eyeBallX);
    sprite.setParameterValueById("ParamEyeBallY", this.current.eyeBallY);
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
