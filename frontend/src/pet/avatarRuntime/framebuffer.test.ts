import { afterEach, describe, expect, it, vi } from "vitest";
import {
  findVisibleBoundsFromApp,
  findVisibleRegionFromApp,
  getPixiCanvas,
  getPixiResolution,
} from "./framebuffer";

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

  it("returns visible bounds and weighted centroid from framebuffer alpha", () => {
    class TestCanvas {
      width = 4;
      height = 3;

      getContext(kind: string) {
        if (kind !== "webgl2") return null;
        return {
          RGBA: 0x1908,
          UNSIGNED_BYTE: 0x1401,
          readPixels: (
            _x: number,
            _y: number,
            w: number,
            h: number,
            _format: number,
            _type: number,
            buffer: Uint8Array,
          ) => {
            const writeAlpha = (cssX: number, cssY: number, alpha: number) => {
              const row = h - 1 - cssY;
              buffer[(row * w + cssX) * 4 + 3] = alpha;
            };
            writeAlpha(0, 1, 60);
            writeAlpha(3, 1, 180);
          },
        };
      }
    }
    vi.stubGlobal("HTMLCanvasElement", TestCanvas);

    const app = { canvas: new TestCanvas(), renderer: { resolution: 1 } };
    const sample = findVisibleRegionFromApp(app, { x: 0, y: 0, w: 4, h: 3 });

    expect(sample?.bounds).toEqual({ x: 0, y: 1, w: 4, h: 1 });
    expect(sample?.centroid.x).toBeCloseTo(2.75);
    expect(sample?.centroid.y).toBeCloseTo(1.5);
    expect(findVisibleBoundsFromApp(app, { x: 0, y: 0, w: 4, h: 3 })).toEqual({
      x: 0,
      y: 1,
      w: 4,
      h: 1,
    });
  });
});
