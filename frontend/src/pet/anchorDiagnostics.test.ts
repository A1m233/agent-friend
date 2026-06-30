import { describe, expect, it } from "vitest";

import { buildAnchorDiagnostics, centerX } from "./anchorDiagnostics";

describe("anchorDiagnostics", () => {
  it("computes rect center x", () => {
    expect(centerX({ x: 10, y: 0, w: 30, h: 20 })).toBe(25);
    expect(centerX(null)).toBeUndefined();
  });

  it("derives action bar center deltas from diagnostic rects", () => {
    const diagnostics = buildAnchorDiagnostics(
      undefined,
      {
        spriteScreen: { x: 100, y: 200, w: 80, h: 120 },
        visibleBounds: { x: 110, y: 210, w: 40, h: 90 },
        actionBarAnchor: { x: 110, y: 210, w: 40, h: 90 },
        actionBarRect: { x: 90, y: 140, w: 100, h: 44 },
      },
      123,
    );

    expect(diagnostics.centers).toEqual({
      spriteScreenCenterX: 140,
      visibleBoundsCenterX: 130,
      actionBarAnchorCenterX: 130,
      actionBarCenterX: 140,
      actionBarMinusAnchorX: 10,
      actionBarMinusSpriteX: 0,
      actionBarMinusVisibleX: 10,
    });
    expect(diagnostics.updatedAt).toBe(123);
  });

  it("keeps prior rects when a patch updates only one source", () => {
    const first = buildAnchorDiagnostics(
      undefined,
      {
        spriteScreen: { x: 100, y: 0, w: 50, h: 50 },
      },
      1,
    );
    const second = buildAnchorDiagnostics(
      first,
      {
        actionBarComputed: { x: 75, y: 0, w: 100, h: 44 },
      },
      2,
    );

    expect(second.centers.actionBarMinusSpriteX).toBe(0);
    expect(second.updatedAt).toBe(2);
  });
});
