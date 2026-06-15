import { describe, expect, it } from "vitest";
import { slotBoundsHit } from "./slotBoundsHit";

describe("slotBoundsHit · 17a cursor hit-test 兜底（design §3.4）", () => {
  const SLOT = { x: 100, y: 100, w: 200, h: 200 };

  it("内部点命中", () => {
    expect(slotBoundsHit({ x: 200, y: 200 }, SLOT)).toBe(true);
  });

  it("四个外侧点都不命中", () => {
    expect(slotBoundsHit({ x: 50, y: 200 }, SLOT)).toBe(false);
    expect(slotBoundsHit({ x: 200, y: 50 }, SLOT)).toBe(false);
    expect(slotBoundsHit({ x: 350, y: 200 }, SLOT)).toBe(false);
    expect(slotBoundsHit({ x: 200, y: 350 }, SLOT)).toBe(false);
  });

  it("边界点命中（含等号边界）", () => {
    expect(slotBoundsHit({ x: 100, y: 100 }, SLOT)).toBe(true);
    expect(slotBoundsHit({ x: 300, y: 300 }, SLOT)).toBe(true);
    expect(slotBoundsHit({ x: 100, y: 300 }, SLOT)).toBe(true);
    expect(slotBoundsHit({ x: 300, y: 100 }, SLOT)).toBe(true);
  });

  it("多屏左屏负坐标 bounds 也工作", () => {
    // sprite 在多屏左屏：bounds 起点 x 为负
    const negSlot = { x: -500, y: 200, w: 160, h: 160 };
    expect(slotBoundsHit({ x: -420, y: 280 }, negSlot)).toBe(true);
    expect(slotBoundsHit({ x: -600, y: 280 }, negSlot)).toBe(false);
  });
});
