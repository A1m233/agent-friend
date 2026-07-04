import { describe, expect, it } from "vitest";

import {
  applyPointOffset,
  applyRegionOffset,
  computeCenteringDelta,
  computePointDelta,
  offsetRegionFromBase,
  offsetPointFromBase,
  resolvePlacementAnchorPoint,
  resolvePlacementAnchorRegion,
  translateRegion,
} from "./spine38Placement";
import type { PixelRegion } from "@/pet/petAlphaHitTest";

function center(region: PixelRegion): { x: number; y: number } {
  return {
    x: region.x + region.w / 2,
    y: region.y + region.h / 2,
  };
}

describe("spine38Placement", () => {
  it("aligns edge placement by visible anchor bounds instead of layout bounds", () => {
    const normalLayout: PixelRegion = { x: 600, y: 100, w: 400, h: 500 };
    const normalVisible: PixelRegion = { x: 840, y: 180, w: 120, h: 320 };
    const miniLayout: PixelRegion = { x: 700, y: 200, w: 200, h: 250 };
    const anchorOffset = offsetRegionFromBase(normalVisible, normalLayout, {
      x: 1,
      y: 1,
    });
    const placementPoint = { x: 1900, y: 360 };

    const miniVisible = resolvePlacementAnchorRegion(miniLayout, anchorOffset, {
      x: 0.5,
      y: 0.5,
    });
    const delta = computeCenteringDelta(miniVisible, placementPoint);
    expect(delta).toEqual({ dx: 1050, dy: 40 });

    const placedLayout = translateRegion(miniLayout, delta?.dx ?? 0, delta?.dy ?? 0);
    const placedVisible = applyRegionOffset(placedLayout, anchorOffset, {
      x: 0.5,
      y: 0.5,
    });
    expect(center(placedVisible)).toEqual(placementPoint);
    expect(center(placedLayout).x).not.toBe(placementPoint.x);
  });

  it("falls back to layout bounds when visible anchor offset is unavailable", () => {
    const layout: PixelRegion = { x: 700, y: 200, w: 200, h: 250 };
    const anchor = resolvePlacementAnchorRegion(layout, null, { x: 0.5, y: 0.5 });

    expect(anchor).toBe(layout);
  });

  it("aligns edge placement by visual centroid when available", () => {
    const normalLayout: PixelRegion = { x: 600, y: 100, w: 400, h: 500 };
    const normalVisible: PixelRegion = { x: 760, y: 180, w: 200, h: 320 };
    const normalCentroid = { x: 930, y: 340 };
    const miniLayout: PixelRegion = { x: 700, y: 200, w: 200, h: 250 };
    const visibleOffset = offsetRegionFromBase(normalVisible, normalLayout, {
      x: 1,
      y: 1,
    });
    const centroidOffset = offsetPointFromBase(normalCentroid, normalLayout, {
      x: 1,
      y: 1,
    });
    const placementPoint = { x: 1900, y: 360 };

    const miniCentroid = resolvePlacementAnchorPoint(miniLayout, centroidOffset, {
      x: 0.5,
      y: 0.5,
    });
    const delta = computePointDelta(miniCentroid, placementPoint);
    expect(delta).toEqual({ dx: 1035, dy: 40 });

    const placedLayout = translateRegion(miniLayout, delta?.dx ?? 0, delta?.dy ?? 0);
    const placedVisible = applyRegionOffset(placedLayout, visibleOffset, {
      x: 0.5,
      y: 0.5,
    });
    const placedCentroid = applyPointOffset(placedLayout, centroidOffset, {
      x: 0.5,
      y: 0.5,
    });
    expect(placedCentroid).toEqual(placementPoint);
    expect(center(placedVisible).x).not.toBe(placementPoint.x);
  });
});
