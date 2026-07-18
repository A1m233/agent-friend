import { afterEach, describe, expect, it, vi } from "vitest";
import {
  findAdaptiveVisibleRegionFromApp,
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

  it("expands beyond geometry bounds to include detached rendered pixels", () => {
    class TestCanvas {
      width = 12;
      height = 12;

      getContext(kind: string) {
        if (kind !== "webgl2") return null;
        return {
          RGBA: 0x1908,
          UNSIGNED_BYTE: 0x1401,
          readPixels: (
            readX: number,
            readY: number,
            w: number,
            h: number,
            _format: number,
            _type: number,
            buffer: Uint8Array,
          ) => {
            for (let cssY = 2; cssY <= 9; cssY += 1) {
              const cssX = 5;
              const glY = this.height - 1 - cssY;
              if (
                cssX < readX ||
                cssX >= readX + w ||
                glY < readY ||
                glY >= readY + h
              ) {
                continue;
              }
              const col = cssX - readX;
              const row = glY - readY;
              buffer[(row * w + col) * 4 + 3] = 255;
            }
          },
        };
      }
    }
    vi.stubGlobal("HTMLCanvasElement", TestCanvas);

    const app = { canvas: new TestCanvas(), renderer: { resolution: 1 } };
    const sample = findAdaptiveVisibleRegionFromApp(
      app,
      { x: 4, y: 5, w: 3, h: 4 },
      { paddingRatio: 1, minPaddingPx: 0 },
    );

    expect(sample?.bounds).toEqual({ x: 5, y: 2, w: 1, h: 8 });
  });
});
