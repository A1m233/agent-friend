import { describe, expect, it } from "vitest";

import {
  computeMonitorViewports,
  resolvePetViewportForRect,
} from "./petMonitorViewport";
import type { PetViewport } from "./petDisplayProfile";

describe("petMonitorViewport · monitor-aware pet viewport", () => {
  it("maps physical side-by-side monitors into overlay CSS coordinates", () => {
    const viewports = computeMonitorViewports(
      [
        { position: { x: 0, y: 0 }, size: { width: 1920, height: 1080 } },
        { position: { x: 1920, y: 0 }, size: { width: 1920, height: 1080 } },
      ],
      { w: 3840, h: 1080 },
    );

    expect(viewports).toEqual([
      { x: 0, y: 0, w: 1920, h: 1080 },
      { x: 1920, y: 0, w: 1920, h: 1080 },
    ]);
  });

  it("normalizes negative monitor origins relative to the overlay origin", () => {
    const viewports = computeMonitorViewports(
      [
        { position: { x: -2560, y: 0 }, size: { width: 2560, height: 1440 } },
        { position: { x: 0, y: 360 }, size: { width: 1920, height: 1080 } },
      ],
      { w: 2240, h: 720 },
    );

    expect(viewports[0]).toEqual({ x: 0, y: 0, w: 1280, h: 720 });
    expect(viewports[1]).toEqual({ x: 1280, y: 180, w: 960, h: 540 });
  });

  it("selects the monitor containing most of the pet rect", () => {
    const left: PetViewport = { x: 0, y: 0, w: 1920, h: 1080 };
    const right: PetViewport = { x: 1920, y: 0, w: 1920, h: 1080 };

    expect(
      resolvePetViewportForRect(
        { x: 1800, y: 400, w: 100, h: 100 },
        [left, right],
        { x: 0, y: 0, w: 3840, h: 1080 },
      ),
    ).toBe(left);

    expect(
      resolvePetViewportForRect(
        { x: 1880, y: 400, w: 100, h: 100 },
        [left, right],
        { x: 0, y: 0, w: 3840, h: 1080 },
      ),
    ).toBe(right);
  });

  it("falls back to nearest monitor when the pet is in a gap", () => {
    const left: PetViewport = { x: 0, y: 0, w: 1000, h: 800 };
    const right: PetViewport = { x: 1400, y: 0, w: 1000, h: 800 };

    expect(
      resolvePetViewportForRect(
        { x: 1250, y: 300, w: 80, h: 80 },
        [left, right],
        { x: 0, y: 0, w: 2400, h: 800 },
      ),
    ).toBe(right);
  });

  it("filters invalid monitor viewports after coordinate normalization", () => {
    const viewports = computeMonitorViewports(
      [
        { position: { x: 0, y: 0 }, size: { width: 1920, height: 1080 } },
        { position: { x: 1920, y: 0 }, size: { width: NaN, height: 1080 } },
      ],
      { w: 1920, h: 1080 },
    );

    expect(viewports).toEqual([{ x: 0, y: 0, w: 1920, h: 1080 }]);
  });

  it("ignores invalid selected rects and monitor viewports", () => {
    const fallback: PetViewport = { x: 0, y: 0, w: 2400, h: 800 };
    const valid: PetViewport = { x: 0, y: 0, w: 1000, h: 800 };
    const invalid: PetViewport = { x: 1400, y: 0, w: NaN, h: 800 };

    expect(
      resolvePetViewportForRect(
        { x: NaN, y: 300, w: 80, h: 80 },
        [valid, invalid],
        fallback,
      ),
    ).toBe(fallback);

    expect(
      resolvePetViewportForRect(
        { x: 1300, y: 300, w: 80, h: 80 },
        [invalid, valid],
        fallback,
      ),
    ).toBe(valid);
  });
});
