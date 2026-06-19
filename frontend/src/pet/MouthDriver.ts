/**
 * 18 R-4.4 · 嘴部参数驱动器（lip-sync）的接口抽象 + 文本 cadence 实现 + audio 占位。
 *
 * 接口让 speaking 态生命周期内的嘴部驱动**可换源**：
 * - 本期：`TextCadenceMouthDriver`——按 `text_delta` 节奏 / token 时长打节拍开闭嘴
 * - 17c 计划：`AudioRmsMouthDriver`——接 voice_bridge audio out / 火山 RTC PCM 流做滑窗 RMS
 *
 * 状态机在 enter speaking 时 `driver.attach(sprite)` + 把 push event 中 text_delta 转发到
 * `onTextDelta`；exit speaking 时 `driver.detach()`，driver 内部把 mouth 参数回零 + 清 timer。
 *
 * 024 改造：driver 不再自己 hook `sprite.onRender`，而是实现 `ParamSource` 接口，由
 * `composePetRender` 统一在 onRender 中按顺序调用 `apply(sprite)`。这样 gaze / tap / drag
 * 等 source 不会互相覆盖 onRender。
 *
 * 详见 18 design §3.5 / §7、024 design §4.5。
 */

import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./sources/types";

export interface MouthDriver extends ParamSource {
  /** 由状态机在 enter speaking 态时调；driver 拿到 sprite handle 准备驱动 mouth 参数。 */
  attach(sprite: Live2DSprite): void;

  /** 文本类 driver 实现；speaking 态期间从 push event 转发过来的 text_delta。 */
  onTextDelta?(text: string): void;

  /** 音频类 driver 未来实现（17c 接 voice_bridge）；speaking 态期间 PCM 帧。 */
  onAudioFrame?(pcm: Float32Array, sampleRate: number): void;

  /** 由状态机在 exit speaking 态时调；driver 把 mouth 参数回零 + 释放内部 timer / 资源。 */
  detach(): void;
}

/** Live2D 标准嘴部参数 ID（Cubism 4 模型通用，含 Hiyori sample）。 */
const PARAM_MOUTH_OPEN_Y = "ParamMouthOpenY";

/** 每字假设嘴形持续时长（ms）；实际值后续可由 design / 实测调优。 */
const MS_PER_CHAR = 80;

/** 单段最小持续时长（ms），避免极短 text_delta（如标点）也整出一次开闭嘴。 */
const MIN_DURATION_MS = 100;

/** sin 波峰值 mouth 张开度（Live2D 参数取值范围一般 [0, 1]）。 */
const MOUTH_OPEN_PEAK = 0.8;

/**
 * 文本 cadence 驱动器：每条 text_delta 在估算时长内驱动 `ParamMouthOpenY` 走一次 sin 波。
 *
 * 多条 text_delta 串行（前一段结束才开始下一段）；不在驱嘴中时新 text_delta 立即启动。
 *
 * 17b 实现节选：算法**最简版**，未做 audio crossfade / 韵律 / 重音；产品验证如不满意，
 * 在 driver 内升级（不动调用方），或直接 17c 切到 `AudioRmsMouthDriver`。
 */
export class TextCadenceMouthDriver implements MouthDriver {
  private sprite: Live2DSprite | null = null;
  private rafId: number | null = null;
  private queue: string[] = [];
  /** raf step 计算出的当前 mouth open 目标值（0..1），由 composer 的 apply 真正 set 到 sprite。 */
  private currentMouthValue = 0;
  /** dev only · 首次 setMouthOpen 时打日志（验证参数 ID + sprite API 真生效） */
  private setMouthOpenLogged = false;

  attach(sprite: Live2DSprite): void {
    this.sprite = sprite;
    if (import.meta.env.DEV) {
      console.info("[lip-sync] driver attached, sprite=", !!sprite);
    }
    // 024：onRender hook 移除，改由 composePetRender 统一调度 apply
  }

  onTextDelta(text: string): void {
    if (!this.sprite || text.length === 0) return;
    if (import.meta.env.DEV && this.queue.length === 0 && this.rafId === null) {
      console.info("[lip-sync] onTextDelta first chunk:", text.slice(0, 10));
    }
    this.queue.push(text);
    if (this.rafId === null) {
      this.tickQueue();
    }
  }

  private tickQueue(): void {
    const next = this.queue.shift();
    if (!next || !this.sprite) {
      this.rafId = null;
      return;
    }
    const durationMs = Math.max(MIN_DURATION_MS, next.length * MS_PER_CHAR);
    const startTs = performance.now();
    const step = (): void => {
      if (!this.sprite) {
        this.rafId = null;
        return;
      }
      const elapsed = performance.now() - startTs;
      if (elapsed >= durationMs) {
        this.setMouthOpen(0);
        this.rafId = null;
        this.tickQueue();
        return;
      }
      const t = elapsed / durationMs;
      const open = Math.sin(t * Math.PI) * MOUTH_OPEN_PEAK;
      this.setMouthOpen(open);
      this.rafId = requestAnimationFrame(step);
    };
    this.rafId = requestAnimationFrame(step);
  }

  private setMouthOpen(v: number): void {
    this.currentMouthValue = v;
    if (import.meta.env.DEV && !this.setMouthOpenLogged) {
      this.setMouthOpenLogged = true;
      try {
        const range = this.sprite?.getParameterValueRangeById?.(PARAM_MOUTH_OPEN_Y);
        console.info(
          `[lip-sync] setMouthOpen(${v.toFixed(2)}) first call · param "${PARAM_MOUTH_OPEN_Y}" range=`,
          range,
        );
      } catch (e) {
        console.warn("[lip-sync] getParameterValueRangeById threw:", e);
      }
    }
    // 024：不再直接 setParameterValueById；由 composer 在 onRender 后调 apply 写入，
    // 确保 motion apply 之后我赢。
  }

  get active(): boolean {
    return this.sprite !== null;
  }

  apply(sprite: Live2DSprite): void {
    if (this.sprite !== sprite) return;
    sprite.setParameterValueById(PARAM_MOUTH_OPEN_Y, this.currentMouthValue);
  }

  detach(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.queue = [];
    this.currentMouthValue = 0;
    if (this.sprite) {
      // 显式 set 0 让嘴回零
      this.sprite.setParameterValueById(PARAM_MOUTH_OPEN_Y, 0);
    }
    this.sprite = null;
    if (import.meta.env.DEV) {
      console.info("[lip-sync] driver detached");
    }
  }
}

/**
 * 17c · 未来接 voice_bridge audio out / 火山 RTC PCM 流时实现。本期为 stub（占扩展位）。
 *
 * 接口签名 + attach/detach 钩子已落，未来仅需把 `onAudioFrame` 实现填上（PCM → 滑窗 RMS
 * → mouth 参数），不改动 MouthDriver 接口 / 状态机 / 调用方代码。
 */
export class AudioRmsMouthDriver implements MouthDriver {
  private sprite: Live2DSprite | null = null;

  get active(): boolean {
    return this.sprite !== null;
  }

  apply(): void {
    // TODO 17c · audio 驱动真正写 mouth 参数
  }

  attach(_sprite: Live2DSprite): void {
    this.sprite = _sprite;
  }

  onAudioFrame(_pcm: Float32Array, _sampleRate: number): void {
    /* TODO 17c · 滑窗 RMS → mouth 参数 */
  }

  detach(): void {
    this.sprite = null;
  }
}
