/**
 * 17a · 操作栏 sprite-relative 浮动定位计算（design §5.4 / R-4.5）。
 *
 * 默认贴 sprite 上方 + 水平中线对齐 sprite 中线；屏顶贴墙时翻到下方（与 016
 * `compute_bubble_position` 翻转语义对称）。
 *
 * 坐标系：CSS px，原点 = pet 整屏 webview viewport 左上 = monitors union 起点。
 * 前端不需要知道屏幕物理坐标 / DPR / multi-monitor 起点；那些由 Rust 端在
 * update_sprite_pos 拼接（设计 §3.2 / §5.3 末段）。
 */
export function computeActionBarPosition(
  sprite: { x: number; y: number; w: number; h: number },
  bar: { w: number; h: number },
  margin = 8,
): { left: number; top: number } {
  // 水平：bar 中线对齐 sprite bounding box 中线
  const spriteCx = sprite.x + sprite.w / 2;
  const left = Math.round(spriteCx - bar.w / 2);

  // 垂直：默认贴上方；above < margin 时翻到下方（防止 bar 挤出屏顶）
  const above = sprite.y - bar.h - margin;
  const below = sprite.y + sprite.h + margin;
  const top = above >= margin ? above : below;

  return { left, top };
}
