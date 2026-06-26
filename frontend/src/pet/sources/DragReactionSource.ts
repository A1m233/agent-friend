import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

/**
 * 024 · 拖拽视觉反馈。
 *
 * 拖拽位置仍由 PIXI slot 直接跟手；这里只负责 Live2D 参数层的反馈。
 * 输入速度先做非线性整形，再用一个小二阶 spring 追目标值；松手时 target 回 0，
 * 保留当前速度自然回弹，避免原先“速度线性映射 + 300ms 线性归零”的机械感。
 */
const ANGLE_Z_PEAK = 14;
const BODY_ANGLE_Z_PEAK = 8;
const BROW_FORM_PEAK = 0.8;
const BROW_Y_PEAK = 0.55;
const MOUTH_FORM_PEAK = -0.55;

const LEAN_STIFFNESS = 95;
const LEAN_DAMPING = 11;
const EXPRESSION_STIFFNESS = 130;
const EXPRESSION_DAMPING = 22;
const MAX_STEP_SECONDS = 1 / 30;
const SETTLE_EPSILON = 0.002;
const SETTLE_VELOCITY_EPSILON = 0.025;

export class DragReactionSource implements ParamSource {
  private isDragging = false;
  private targetLean = 0;
  private targetExpression = 0;
  private lean = 0;
  private leanVelocity = 0;
  private expression = 0;
  private expressionVelocity = 0;
  private lastStepTs: number | null = null;

  setDragging(v: boolean, vx = 0, vy = 0): void {
    if (v) {
      this.isDragging = true;
      this.setTargets(vx, vy);
      this.lastStepTs = null;
      if (import.meta.env.DEV) {
        console.info("[drag] start", { vx, vy });
      }
    } else if (this.isDragging) {
      this.isDragging = false;
      this.targetLean = 0;
      this.targetExpression = 0;
      if (import.meta.env.DEV) {
        console.info("[drag] end");
      }
    }
  }

  /** 拖动中由 slot 喂"瞬时方向"（normalized）。 */
  updateDragDirection(vx: number, vy: number): void {
    if (!this.isDragging) return;
    this.setTargets(vx, vy);
  }

  get active(): boolean {
    if (this.isDragging) return true;
    return !this.isSettled();
  }

  apply(sprite: Live2DSprite): void {
    this.step(performance.now());
    if (!this.isDragging && this.isSettled()) this.reset();

    const lean = clamp(this.lean, -1.15, 1.15);
    const expression = clamp(this.expression, 0, 1);
    sprite.setParameterValueById("ParamAngleZ", ANGLE_Z_PEAK * lean);
    sprite.setParameterValueById("ParamBodyAngleZ", BODY_ANGLE_Z_PEAK * lean);
    sprite.setParameterValueById("ParamBrowLForm", BROW_FORM_PEAK * expression);
    sprite.setParameterValueById("ParamBrowRForm", BROW_FORM_PEAK * expression);
    sprite.setParameterValueById("ParamBrowLY", BROW_Y_PEAK * expression);
    sprite.setParameterValueById("ParamBrowRY", BROW_Y_PEAK * expression);
    sprite.setParameterValueById("ParamMouthForm", MOUTH_FORM_PEAK * expression);
  }

  private setTargets(vx: number, vy: number): void {
    const x = clamp(vx, -1, 1);
    const y = clamp(vy, -1, 1);
    this.targetLean = shapeSignedInput(x);
    this.targetExpression = shapeUnitInput(clamp(Math.hypot(x, y), 0, 1));
  }

  private step(now: number): void {
    if (this.lastStepTs === null) {
      this.lastStepTs = now;
      return;
    }

    const dt = clamp((now - this.lastStepTs) / 1000, 0, MAX_STEP_SECONDS);
    this.lastStepTs = now;
    if (dt === 0) return;

    const leanNext = stepSpring(
      this.lean,
      this.leanVelocity,
      this.targetLean,
      dt,
      LEAN_STIFFNESS,
      LEAN_DAMPING,
    );
    this.lean = leanNext.value;
    this.leanVelocity = leanNext.velocity;

    const expressionNext = stepSpring(
      this.expression,
      this.expressionVelocity,
      this.targetExpression,
      dt,
      EXPRESSION_STIFFNESS,
      EXPRESSION_DAMPING,
    );
    this.expression = expressionNext.value;
    this.expressionVelocity = expressionNext.velocity;
  }

  private isSettled(): boolean {
    return (
      Math.abs(this.lean) < SETTLE_EPSILON &&
      Math.abs(this.leanVelocity) < SETTLE_VELOCITY_EPSILON &&
      Math.abs(this.expression) < SETTLE_EPSILON &&
      Math.abs(this.expressionVelocity) < SETTLE_VELOCITY_EPSILON
    );
  }

  private reset(): void {
    this.targetLean = 0;
    this.targetExpression = 0;
    this.lean = 0;
    this.leanVelocity = 0;
    this.expression = 0;
    this.expressionVelocity = 0;
    this.lastStepTs = null;
  }
}

function shapeSignedInput(v: number): number {
  return Math.sign(v) * shapeUnitInput(Math.abs(v));
}

function shapeUnitInput(v: number): number {
  const x = clamp(v, 0, 1);
  return x * x * (3 - 2 * x);
}

function stepSpring(
  value: number,
  velocity: number,
  target: number,
  dt: number,
  stiffness: number,
  damping: number,
): { value: number; velocity: number } {
  const acceleration = stiffness * (target - value) - damping * velocity;
  const nextVelocity = velocity + acceleration * dt;
  const nextValue = value + nextVelocity * dt;
  return { value: nextValue, velocity: nextVelocity };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
