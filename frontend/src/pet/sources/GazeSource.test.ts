import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GazeSource } from "./GazeSource";

function makeMockSprite(): {
  setParameterValueById: ReturnType<typeof vi.fn>;
} {
  return {
    setParameterValueById: vi.fn(),
  } as unknown as {
    setParameterValueById: ReturnType<typeof vi.fn>;
  };
}

function makeSpriteScreen(): { x: number; y: number; w: number; h: number } {
  return { x: 500, y: 300, w: 320, h: 360 };
}

describe("GazeSource", () => {
  let nowMs = 0;
  let nowSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    nowMs = 0;
    vi.stubGlobal("window", { innerWidth: 1920, innerHeight: 1080 });
    nowSpy = vi.spyOn(globalThis.performance, "now").mockImplementation(() => {
      nowMs += 16;
      return nowMs;
    });
  });

  afterEach(() => {
    nowSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it("updateCursor：光标在桌宠正中 → target 全为 0", () => {
    const src = new GazeSource();
    const sprite = makeSpriteScreen();
    src.updateCursor(sprite.x + sprite.w / 2, sprite.y + sprite.h / 2, sprite);

    const sp = makeMockSprite();
    src.apply(sp as unknown as import("easy-live2d").Live2DSprite);
    const lastAngleX = getLastParamValue(sp.setParameterValueById, "ParamAngleX");
    const lastAngleY = getLastParamValue(sp.setParameterValueById, "ParamAngleY");
    expect(lastAngleX).toBeCloseTo(0, 1);
    expect(lastAngleY).toBeCloseTo(0, 1);
  });

  it("updateCursor：光标在阈值内右侧 → angleX / angleY 符号正确", () => {
    const src = new GazeSource();
    const sprite = makeSpriteScreen();
    // sprite 中心 (660, 480)；阈值 ~960；放在右下方、距离约 385 处（阈值内）
    src.updateCursor(1000, 300, sprite);

    const sp = makeMockSprite();
    for (let i = 0; i < 100; i++) src.apply(sp as unknown as import("easy-live2d").Live2DSprite);

    const lastAngleX = getLastParamValue(sp.setParameterValueById, "ParamAngleX");
    const lastAngleY = getLastParamValue(sp.setParameterValueById, "ParamAngleY");
    const lastEyeBallX = getLastParamValue(sp.setParameterValueById, "ParamEyeBallX");
    // 光标在右 → 头朝右转 / 眼球看右（angleX / eyeBallX 均正）
    expect(lastAngleX).toBeGreaterThan(5);
    expect(lastAngleX).toBeLessThanOrEqual(45);
    // 光标在 sprite 上方（屏幕 Y 朝下），头向上看 → angleY 正
    expect(lastAngleY).toBeGreaterThan(5);
    expect(lastEyeBallX).toBeGreaterThan(0.2);
  });

  it("updateCursor：光标在阈值外 → angleX 最终归零", () => {
    const src = new GazeSource();
    const sprite = makeSpriteScreen();
    // 足够远，dist > 960 threshold
    src.updateCursor(-1000, -1000, sprite);

    const sp = makeMockSprite();
    for (let i = 0; i < 60; i++) src.apply(sp as unknown as import("easy-live2d").Live2DSprite);

    const lastAngleX = getLastParamValue(sp.setParameterValueById, "ParamAngleX");
    expect(lastAngleX).toBeCloseTo(0, 1);
  });

  it("setActive(false) → active === false", () => {
    const src = new GazeSource();
    expect(src.active).toBe(true);
    src.setActive(false);
    expect(src.active).toBe(false);
  });

  it("apply 多次后 current 经 EMA 收敛到 target", () => {
    const src = new GazeSource();
    const sprite = makeSpriteScreen();
    src.updateCursor(1000, 300, sprite);

    const sp = makeMockSprite();
    for (let i = 0; i < 120; i++) src.apply(sp as unknown as import("easy-live2d").Live2DSprite);

    const lastAngleX = getLastParamValue(sp.setParameterValueById, "ParamAngleX");
    const lastEyeBallX = getLastParamValue(sp.setParameterValueById, "ParamEyeBallX");
    expect(lastAngleX).toBeGreaterThan(5);
    expect(lastEyeBallX).toBeGreaterThan(0.2);
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
