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

  it("setDragging(true) 后通过 spring 追随目标，不再瞬时线性跳到角度", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 1, 0);

    expect(src.active).toBe(true);
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const immediate = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    nowMs = 16;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    expect(immediate).toBeCloseTo(0, 3);
    expect(angleZ).toBeGreaterThan(0);
    expect(angleZ).toBeLessThan(14);
  });

  it("setDragging(false) 后用 spring 回弹并最终收敛到中立", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 1, 0);
    runFrames(src, sp, 45, () => {
      nowMs += 16;
    });
    const peak = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(peak).toBeGreaterThan(8);

    src.setDragging(false);
    nowMs += 16;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const afterRelease = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    runFrames(src, sp, 120, () => {
      nowMs += 16;
    });
    const settled = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");

    expect(Math.abs(afterRelease)).toBeGreaterThan(0);
    expect(Math.abs(settled)).toBeLessThan(0.05);
    expect(src.active).toBe(false);
  });

  it("没有进入实际摇晃时结束拖动，active 立即回到 false", () => {
    const src = new DragReactionSource();
    src.setDragging(true);
    src.setDragging(false);
    expect(src.active).toBe(false);
  });

  it("updateDragDirection 在 dragging 期间更新方向；非 dragging 期间不改变状态", () => {
    const src = new DragReactionSource();

    src.updateDragDirection(-1, 0);
    expect(src.active).toBe(false);

    src.setDragging(true, 1, 0);
    src.updateDragDirection(-1, 0);
    const sp = makeMockSprite();
    runFrames(src, sp, 45, () => {
      nowMs += 16;
    });
    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(angleZ).toBeLessThan(-8);
  });

  it("参数 clamp 在 [-1, 1]", () => {
    const src = new DragReactionSource();
    const sp = makeMockSprite();
    src.setDragging(true, 5, -5);
    runFrames(src, sp, 60, () => {
      nowMs += 16;
    });
    const angleZ = getLastParamValue(sp.setParameterValueById, "ParamAngleZ");
    expect(angleZ).toBeGreaterThan(8);
    expect(angleZ).toBeLessThanOrEqual(16.1);
  });
});

function runFrames(
  src: DragReactionSource,
  sp: { setParameterValueById: ReturnType<typeof vi.fn> },
  count: number,
  advance: () => void,
): void {
  for (let i = 0; i < count; i += 1) {
    advance();
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
  }
}

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
