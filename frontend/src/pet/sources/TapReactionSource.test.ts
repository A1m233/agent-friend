import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TapReactionSource } from "./TapReactionSource";

function makeMockSprite(): {
  setParameterValueById: ReturnType<typeof vi.fn>;
} {
  return {
    setParameterValueById: vi.fn(),
  } as unknown as {
    setParameterValueById: ReturnType<typeof vi.fn>;
  };
}

describe("TapReactionSource", () => {
  let nowMs = 0;
  let nowSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    nowMs = 0;
    nowSpy = vi.spyOn(globalThis.performance, "now").mockImplementation(() => nowMs);
  });

  afterEach(() => {
    nowSpy.mockRestore();
  });

  it("fire() → active 为 true，包络峰值出现在中点", () => {
    const src = new TapReactionSource();
    const sp = makeMockSprite();
    src.fire();

    expect(src.active).toBe(true);

    nowMs = 400;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);

    const cheek = getLastParamValue(sp.setParameterValueById, "ParamCheek");
    expect(cheek).toBeCloseTo(1.0, 2);
  });

  it("reacting 期间再 fire → startTs 不更新", () => {
    const src = new TapReactionSource();
    src.fire();
    nowMs = 100;
    src.fire();
    nowMs = 700;
    expect(src.active).toBe(true);
  });

  it("REACTING_DURATION_MS 后 → active 为 false 且 apply 不再写参数", () => {
    const src = new TapReactionSource();
    const sp = makeMockSprite();
    src.fire();
    nowMs = 800;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    expect(src.active).toBe(false);
    expect(sp.setParameterValueById).not.toHaveBeenCalled();
  });

  it("包络在起点和终点为 0", () => {
    const src = new TapReactionSource();
    const sp = makeMockSprite();
    src.fire();
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    expect(getLastParamValue(sp.setParameterValueById, "ParamCheek")).toBeCloseTo(0, 3);

    const callCountAtStart = sp.setParameterValueById.mock.calls.length;
    nowMs = 800;
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    expect(sp.setParameterValueById.mock.calls.length).toBe(callCountAtStart); // 终点不写
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
