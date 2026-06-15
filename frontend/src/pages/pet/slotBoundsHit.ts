/**
 * 17a · cursor hit-test 兜底路径（design §3.4）。
 *
 * PIXI alpha readPixels 在 PIXI v8 API 不可达 / 渲染未就绪时使用：avatar-slot
 * `getBounds()` + point-in-rect。占位形象是圆形，bounding box 比 alpha 多命中
 * 约 21% 角区——17a 验收阶段可接受（占位本就是粗糙占位，不要求像素级精确）；
 * 17b 上 Live2D 时视情况升级到 alpha plumbing。
 *
 * 坐标系：CSS px（webview viewport），与 `slot.getBounds()` 在 stage transform =
 * identity 下的输出一致；与 `pet://cursor` event payload (Rust 端 cursor logical px)
 * 同坐标系。
 */
export function slotBoundsHit(
  cursor: { x: number; y: number },
  bounds: { x: number; y: number; w: number; h: number },
): boolean {
  return (
    cursor.x >= bounds.x &&
    cursor.x <= bounds.x + bounds.w &&
    cursor.y >= bounds.y &&
    cursor.y <= bounds.y + bounds.h
  );
}
