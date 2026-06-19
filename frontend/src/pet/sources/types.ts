import type { Live2DSprite } from "easy-live2d";

/**
 * 024 · 可向 Live2D sprite 每帧写入参数的源。
 *
 * 多个 source 可能同时想写同一参数，由 `composePetRender` 按固定顺序依次
 * 调用 apply；越靠后的 source 覆盖越靠前 source 的写入。
 */
export interface ParamSource {
  /** composer 每帧 onRender 时检查；false 时跳过 apply（noop）。 */
  readonly active: boolean;
  /** motion apply 之后，由 composer 在 onRender 内调；source 负责 setParameterValueById。 */
  apply(sprite: Live2DSprite): void;
}
