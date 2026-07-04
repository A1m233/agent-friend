/**
 * 18 · cursor 位置 alpha hit-test —— PIXI canvas 取 cursor 1 像素的 alpha 值，
 * > threshold 才算命中，让 Live2D 形象透明区（手脚间空隙、四角空白）精确穿透。
 *
 * **零配置**：换 Live2D 模型不需要改任何尺寸常量；alpha 直接从 GPU framebuffer 像素推。
 *
 * 性能：1 像素 readPixels 触发 GL fence（CPU 等 GPU 完成当前帧），但 1 像素数据极小，
 * 60Hz cursor channel 下实测可接受。fast-reject region 作为前置过滤可大幅减少 readPixels
 * 调用频度（cursor 不在 spriteScreen 矩形内时直接返 false）。
 *
 * 前置条件：`pixi.init({ preserveDrawingBuffer: true })` —— 否则下一帧前 framebuffer
 * 被清空，readPixels 拿到全 0；见 usePixiAvatarSlot.ts。
 *
 * 详见 18 design §3.4 / [issue ...](TODO 后续登记)。
 */

import type * as PIXI from "pixi.js";
import {
  alphaHitTestFromApp,
  findVisibleBoundsFromApp,
  findVisibleRegionFromApp,
  type VisibleRegionSample,
} from "./avatarRuntime/framebuffer";

/** alpha > 此阈值（0–255）才视为命中。30 (~12%) 经验值，过滤抗锯齿边缘的极淡像素。 */
const ALPHA_THRESHOLD = 30;

interface CursorPos {
  /** CSS px，与 `pet://cursor` payload 同坐标系（webview viewport）。 */
  x: number;
  y: number;
}

export interface PixelRegion {
  /** CSS px，与 `slot.getBounds()` 同坐标系。 */
  x: number;
  y: number;
  w: number;
  h: number;
}

export type { VisibleRegionSample };

/**
 * 取 PIXI canvas 上 cursor 位置的 alpha 值，判断是否命中不透明像素。
 *
 * @returns true = 命中（alpha > threshold）；false = 未命中（透明 / 失败 / 未就绪）
 */
export function alphaHitTest(
  app: PIXI.Application | null,
  cursor: CursorPos,
): boolean {
  return alphaHitTestFromApp(app, cursor, ALPHA_THRESHOLD);
}

/**
 * 18 · 扫一段区域的 framebuffer，找 alpha > threshold 像素的最 minX/minY/maxX/maxY，
 * 返回 visible bounds（world CSS px）。换 Live2D 模型零硬编码——不论模型 canvas
 * 内有多少 padding，visible bounds 都自动适配为像素轮廓。
 *
 * 一次性 `gl.readPixels(w × h)` 整块拉到 CPU buffer，CPU 端扫描；vs 多次小读取更高效
 * （GPU→CPU 同步只一次）。192K 像素 × 4 byte ≈ 768KB / 次，每 0.5s 调一次开销可忽略。
 *
 * @param app PIXI Application
 * @param region 要扫的区域（CSS px，与 slot.getBounds() 同坐标系）
 * @returns 找到 alpha 像素 → 返 visible bounds；全透明 / 失败 → 返 null
 */
export function findVisibleBounds(
  app: PIXI.Application | null,
  region: PixelRegion,
): PixelRegion | null {
  return findVisibleBoundsFromApp(app, region, ALPHA_THRESHOLD);
}

export function findVisibleRegion(
  app: PIXI.Application | null,
  region: PixelRegion,
): VisibleRegionSample | null {
  return findVisibleRegionFromApp(app, region, ALPHA_THRESHOLD);
}
