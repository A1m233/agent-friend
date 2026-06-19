import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DragReactionSource } from "./DragReactionSource";

function makeMockSprite(): {
  setParameterValueById: ReturnType<typeof vi.fn>;
} {
  return {
    setParameterValueById: vi.fn(),
  } as unknown as {
    setParameterValueById: ReturnType<typeof vi.fn>;
  };
}

describe("DragReactionSource", () => {
  let nowMs = 0;
  let nowSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    nowMs = 0;
    nowSpy = vi.spyOn(globalThis.performance, "now").mockImplementation(() => nowMs);
  });

  afterEach(() => {
    nowSpy.mockRestore();
  });

  it("setDragging(true, 0.5, 0) → active + ParamAngleZ 按 dragVelX 算", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 0.5, 0);

    expect(src.active).toBe(true);
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);

    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(angleZ).toBeCloseTo(12.5, 1); // 25 * 0.5
  });

  it("setDragging(false) → release 阶段 envelope 线性衰减", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 1, 0);
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const peak = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    src.setDragging(false);
    nowMs = 150;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const mid = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    expect(peak).toBeCloseTo(25, 1);
    expect(mid).toBeCloseTo(12.5, 1);
  });

  it("RELEASE_DURATION_MS 后 → active 为 false", () => {
    const src = new DragReactionSource();
    src.setDragging(true);
    src.setDragging(false);
    nowMs = 300;
    expect(src.active).toBe(false);
  });

  it("updateDragDirection 在 dragging 期间更新方向；非 dragging 期间不改变状态", () => {
    const src = new DragReactionSource();

    src.updateDragDirection(-1, 0);
    expect(src.active).toBe(false);

    src.setDragging(true, 1, 0);
    src.updateDragDirection(-1, 0);
    const sp = makeMockSprite();
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(angleZ).toBeCloseTo(-25, 1);
  });

  it("参数 clamp 在 [-1, 1]", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 5, -5);
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(angleZ).toBeCloseTo(25, 1);
  });
});

function getLastParamValue(
  mockFn: ReturnType<typeof vi.fn>,
  paramId: string,
): number {
  const calls = mockFn.mock.calls as [string, number][];
  for (let i = calls.length - 1; i >= 0; i--) {
    if (calls[i][0] === paramId) return calls[i][1];
  }
  return NaN;
}
