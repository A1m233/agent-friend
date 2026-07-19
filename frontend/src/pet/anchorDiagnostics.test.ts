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

  it("records viewport and dock visibility diagnostics", () => {
    const diagnostics = buildAnchorDiagnostics(
      undefined,
      {
        selectedViewport: { x: 0, y: 0, w: 1000, h: 800 },
        actionBarAnchor: { x: 940, y: 300, w: 120, h: 200 },
        monitorViewports: [
          { x: 0, y: 0, w: 1000, h: 800 },
          { x: 1000, y: 0, w: 1000, h: 800 },
        ],
        displayProfile: {
          avatarSize: "mini",
          placementKey: "normal->mini",
          modelScale: 1,
          modeScale: 0.5,
          renderScale: 0.5,
          placementPoint: { x: 980, y: 400 },
          edge: "right",
        },
      },
      3,
    );

    expect(diagnostics.monitorViewports).toHaveLength(2);
    expect(diagnostics.dock?.selectedViewportVisibleRatioX).toBe(0.5);
    expect(diagnostics.dock?.clippedRight).toBe(60);
    expect(diagnostics.displayProfile?.placementPoint).toEqual({ x: 980, y: 400 });
  });

  it("records placement anchor deltas", () => {
    const diagnostics = buildAnchorDiagnostics(
      undefined,
      {
        placement: {
          layoutBounds: { x: 700, y: 200, w: 200, h: 250 },
          placementAnchor: { x: 820, y: 240, w: 60, h: 160 },
          placementAnchorPoint: { x: 850, y: 320 },
          placementPoint: { x: 980, y: 320 },
          delta: { dx: 130, dy: 0 },
        },
      },
      4,
    );

    expect(diagnostics.placement?.placementAnchor).toEqual({
      x: 820,
      y: 240,
      w: 60,
      h: 160,
    });
    expect(diagnostics.placement?.placementAnchorPoint).toEqual({
      x: 850,
      y: 320,
    });
    expect(diagnostics.placement?.delta).toEqual({ dx: 130, dy: 0 });
  });
});
