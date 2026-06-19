import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

/**
 * 024 · 点击 tap 反应表情。
 *
 * 触发后 800ms 内用 sin 包络驱动面部参数，模拟被点后的
 * 「脸红 + 微笑嘴」反应。眼睛由 motion 本身控制，这里不动开合参数。
 */
const REACTING_DURATION_MS = 800;
const CHEEK_PEAK = 1.0;
const MOUTH_OPEN_PEAK = 0.3;
const MOUTH_FORM_PEAK = 0.5;

export class TapReactionSource implements ParamSource {
  private startTs: number | null = null;

  get active(): boolean {
    if (this.startTs === null) return false;
    return performance.now() - this.startTs < REACTING_DURATION_MS;
  }

  /** click handler 调；reacting 期间忽略后续 fire。 */
  fire(): void {
    if (this.active) return;
    this.startTs = performance.now();
    if (import.meta.env.DEV) {
      console.info("[tap] fire");
    }
  }

  apply(sprite: Live2DSprite): void {
    if (this.startTs === null) return;
    const elapsed = performance.now() - this.startTs;
    if (elapsed >= REACTING_DURATION_MS) return;
    const t = elapsed / REACTING_DURATION_MS;
    const envelope = Math.sin(t * Math.PI);
    sprite.setParameterValueById("ParamCheek", CHEEK_PEAK * envelope);
    sprite.setParameterValueById("ParamMouthOpenY", MOUTH_OPEN_PEAK * envelope);
    sprite.setParameterValueById("ParamMouthForm", MOUTH_FORM_PEAK * envelope);
  }
}
