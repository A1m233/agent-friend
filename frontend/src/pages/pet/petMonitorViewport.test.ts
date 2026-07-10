import { describe, expect, it } from "vitest";

import {
  computeMonitorViewports,
  rebasePetPointByMonitorAnchor,
  resolvePetPointFromMonitorRelativeAnchor,
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

  it("keeps the pet at the same laptop-relative anchor when an external monitor is unplugged", () => {
    const rebased = rebasePetPointByMonitorAnchor({
      point: { x: 1020, y: 995.56 },
      previousBounds: { x: -2560, y: 0, width: 5120, height: 1600 },
      nextBounds: { x: 0, y: 0, width: 2560, height: 1600 },
      previousMonitors: [
        { x: -2560, y: 0, width: 2560, height: 1600, scaleMilli: 1750 },
        { x: 0, y: 0, width: 2560, height: 1440, scaleMilli: 1250 },
      ],
      nextMonitors: [{ x: 0, y: 0, width: 2560, height: 1600, scaleMilli: 1750 }],
      previousOverlaySize: { w: 4096, h: 1280 },
      nextOverlaySize: { w: 2048, h: 1280 },
    });

    expect(rebased?.x).toBeCloseTo(1020);
    expect(rebased?.y).toBeCloseTo(995.56);
  });

  it("moves the anchor with the original monitor when a new monitor appears on the negative side", () => {
    expect(
      rebasePetPointByMonitorAnchor({
        point: { x: 320, y: 240 },
        previousBounds: { x: 0, y: 0, width: 1920, height: 1080 },
        nextBounds: { x: -2560, y: 0, width: 4480, height: 1440 },
        previousMonitors: [{ x: 0, y: 0, width: 1920, height: 1080, scaleMilli: 1000 }],
        nextMonitors: [
          { x: -2560, y: 0, width: 2560, height: 1440, scaleMilli: 1000 },
          { x: 0, y: 0, width: 1920, height: 1080, scaleMilli: 1000 },
        ],
        previousOverlaySize: { w: 1920, h: 1080 },
        nextOverlaySize: { w: 4480, h: 1440 },
      }),
    ).toEqual({ x: 2880, y: 240 });
  });

  it("falls back to the remaining monitor when the selected monitor disappears", () => {
    expect(
      rebasePetPointByMonitorAnchor({
        point: { x: 3000, y: 360 },
        previousBounds: { x: 0, y: 0, width: 3840, height: 1080 },
        nextBounds: { x: 0, y: 0, width: 1920, height: 1080 },
        previousMonitors: [
          { x: 0, y: 0, width: 1920, height: 1080, scaleMilli: 1000 },
          { x: 1920, y: 0, width: 1920, height: 1080, scaleMilli: 1000 },
        ],
        nextMonitors: [{ x: 0, y: 0, width: 1920, height: 1080, scaleMilli: 1000 }],
        previousOverlaySize: { w: 3840, h: 1080 },
        nextOverlaySize: { w: 1920, h: 1080 },
      }),
    ).toEqual({ x: 1080, y: 360 });
  });

  it("returns null when topology payloads are incomplete", () => {
    expect(
      rebasePetPointByMonitorAnchor({
        point: { x: 320, y: 240 },
        previousBounds: { x: 0, y: 0, width: 1920, height: 1080 },
        nextBounds: { x: 0, y: 0, width: 1920, height: 1080 },
        previousMonitors: [],
        nextMonitors: [{ x: 0, y: 0, width: 1920, height: 1080 }],
        previousOverlaySize: { w: 1920, h: 1080 },
        nextOverlaySize: { w: 1920, h: 1080 },
      }),
    ).toBeNull();
  });

  it("materializes a monitor-relative anchor against the latest CSS viewport", () => {
    const point = resolvePetPointFromMonitorRelativeAnchor({
        anchor: {
          monitor: { x: 0, y: 0, width: 2560, height: 1600, scaleMilli: 1750 },
          relativeAnchor: { x: 0.498046875, y: 0.77778125 },
        },
        bounds: { x: 0, y: 0, width: 2560, height: 1600 },
        monitors: [{ x: 0, y: 0, width: 2560, height: 1600, scaleMilli: 1750 }],
        overlaySize: { w: 1463, h: 915 },
      });

    expect(point?.x).toBeCloseTo(728.642578125);
    expect(point?.y).toBeCloseTo(711.66984375);
  });
});
