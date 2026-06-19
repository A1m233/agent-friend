import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./sources/types";

/**
 * 024 · 把多个 ParamSource 统一挂到 sprite.onRender。
 *
 * - 只 hook 一次 onRender，避免多个 source 各自 hook 互相覆盖。
 * - 调用方通过数组顺序表达优先级：越靠后的 source 越晚 apply，可覆盖前面
 *   source 写入的同名参数。
 * - 返回 detach 函数，用于恢复原始 onRender。
 */
export function composePetRender(
  sprite: Live2DSprite,
  sources: readonly ParamSource[],
): () => void {
  const orig = ((sprite as { onRender?: (renderer: unknown) => void }).onRender as
    | ((renderer: unknown) => void)
    | undefined) ?? null;

  (sprite as { onRender: (renderer: unknown) => void }).onRender = (renderer) => {
    orig?.(renderer); // Live2D model.update / motion / breath apply
    for (const src of sources) {
      if (src.active) src.apply(sprite);
    }
  };

  return () => {
    (sprite as { onRender: ((renderer: unknown) => void) | null }).onRender = orig;
  };
}
