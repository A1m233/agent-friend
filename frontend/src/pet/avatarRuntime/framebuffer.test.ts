import { afterEach, describe, expect, it, vi } from "vitest";
import { getPixiCanvas, getPixiResolution } from "./framebuffer";

describe("avatar runtime framebuffer helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("finds canvas from Pixi8 and Pixi7 shaped apps", () => {
    class TestCanvas {}
    vi.stubGlobal("HTMLCanvasElement", TestCanvas);
    const pixi8Canvas = new TestCanvas();
    const pixi7Canvas = new TestCanvas();

    expect(getPixiCanvas({ canvas: pixi8Canvas })).toBe(pixi8Canvas);
    expect(getPixiCanvas({ view: pixi7Canvas })).toBe(pixi7Canvas);
    expect(getPixiCanvas({ renderer: { canvas: pixi8Canvas } })).toBe(pixi8Canvas);
    expect(getPixiCanvas({ renderer: { view: pixi7Canvas } })).toBe(pixi7Canvas);
    expect(getPixiCanvas({})).toBeNull();
  });

  it("normalizes renderer resolution", () => {
    expect(getPixiResolution({ renderer: { resolution: 2 } })).toBe(2);
    expect(getPixiResolution({ renderer: { resolution: 0 } })).toBe(1);
    expect(getPixiResolution({ renderer: { resolution: Number.NaN } })).toBe(1);
    expect(getPixiResolution({})).toBe(1);
  });
});
