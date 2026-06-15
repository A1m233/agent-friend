import { describe, expect, it } from "vitest";
import { computeActionBarPosition } from "./computeActionBarPosition";

describe("computeActionBarPosition · 17a 操作栏 sprite-relative 定位（design §5.4）", () => {
  const SPRITE = { x: 880, y: 460, w: 160, h: 160 }; // 屏幕中央 1920×1080 上的 160×160 sprite
  const BAR = { w: 160, h: 60 };

  it("sprite 居中：bar 默认贴上方 + 水平居中对齐", () => {
    const { left, top } = computeActionBarPosition(SPRITE, BAR, 8);
    // sprite 中线 = 880 + 80 = 960；left = 960 - 80 = 880
    expect(left).toBe(880);
    // above = 460 - 60 - 8 = 392；>= margin → 选 above
    expect(top).toBe(392);
  });

  it("sprite 贴顶 → 翻转到 sprite 下方", () => {
    const sprite = { x: 880, y: 0, w: 160, h: 160 };
    const { top } = computeActionBarPosition(sprite, BAR, 8);
    // above = -68 < margin → 翻 below = 0 + 160 + 8 = 168
    expect(top).toBe(168);
  });

  it("above 恰好 < margin 也翻下方（防止贴边抖动）", () => {
    const sprite = { x: 880, y: 68, w: 160, h: 160 };
    const { top } = computeActionBarPosition(sprite, BAR, 8);
    // above = 68 - 60 - 8 = 0；0 < 8 → 翻 below
    expect(top).toBe(68 + 160 + 8);
  });

  it("sprite 较小时仍水平居中（bar 比 sprite 宽）", () => {
    const sprite = { x: 100, y: 200, w: 50, h: 50 };
    const { left } = computeActionBarPosition(sprite, BAR, 8);
    // sprite 中线 = 100 + 25 = 125；left = 125 - 80 = 45
    expect(left).toBe(45);
  });

  it("多屏左屏负坐标 sprite", () => {
    // 多屏：左屏 x 起点 = -2560
    const sprite = { x: -2400, y: 460, w: 160, h: 160 };
    const { left, top } = computeActionBarPosition(sprite, BAR, 8);
    expect(left).toBe(-2400 + 80 - 80); // -2400
    expect(top).toBe(460 - 60 - 8);
  });
});
