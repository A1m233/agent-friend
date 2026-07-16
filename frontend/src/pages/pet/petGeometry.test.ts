import { describe, expect, it } from "vitest";

import {
  centerPointOfRect,
  hasReachedPlacementPoint,
  isFinitePoint,
  isFiniteRect,
  isFiniteViewport,
  samePoint,
  sameRect,
  sameViewportList,
} from "./petGeometry";

describe("petGeometry · finite guards and same-value checks", () => {
  it("accepts finite positive rectangles and rejects NaN or empty geometry", () => {
    expect(isFiniteRect({ x: 1, y: 2, w: 3, h: 4 })).toBe(true);
    expect(isFiniteRect({ x: NaN, y: 2, w: 3, h: 4 })).toBe(false);
    expect(isFiniteRect({ x: 1, y: 2, w: 0, h: 4 })).toBe(false);
  });

  it("accepts only finite points", () => {
    expect(isFinitePoint({ x: 1, y: 2 })).toBe(true);
    expect(isFinitePoint({ x: Number.POSITIVE_INFINITY, y: 2 })).toBe(false);
  });

  it("uses the same positive finite rules for viewports", () => {
    expect(isFiniteViewport({ x: 0, y: 0, w: 1920, h: 1080 })).toBe(true);
    expect(isFiniteViewport({ x: 0, y: 0, w: 1920, h: NaN })).toBe(false);
  });

  it("treats sub-pixel equivalent points and rects as unchanged", () => {
    expect(samePoint({ x: 10, y: 20 }, { x: 10.2, y: 19.8 })).toBe(true);
    expect(sameRect(
      { x: 1, y: 2, w: 3, h: 4 },
      { x: 1.1, y: 2.2, w: 3.1, h: 4.2 },
    )).toBe(true);
  });

  it("compares monitor viewport lists structurally", () => {
    expect(sameViewportList(
      [{ x: 0, y: 0, w: 1920, h: 1080 }],
      [{ x: 0.2, y: 0, w: 1920, h: 1080 }],
    )).toBe(true);
    expect(sameViewportList(
      [{ x: 0, y: 0, w: 1920, h: 1080 }],
      [{ x: 1920, y: 0, w: 1920, h: 1080 }],
    )).toBe(false);
  });

  it("computes rect centers in CSS pixel coordinates", () => {
    expect(centerPointOfRect({ x: 10, y: 20, w: 100, h: 60 })).toEqual({
      x: 60,
      y: 50,
    });
  });

  it("consumes a placement intent using the visible anchor instead of layout bounds", () => {
    const target = { x: 900, y: 500 };
    const spriteScreen = { x: 100, y: 100, w: 1200, h: 700 };
    const actionBarAnchor = { x: 800, y: 350, w: 200, h: 300 };

    expect(centerPointOfRect(spriteScreen)).not.toEqual(target);
    expect(hasReachedPlacementPoint(target, actionBarAnchor, spriteScreen)).toBe(true);
  });

  it("falls back to sprite bounds when a runtime has no separate visible anchor", () => {
    expect(
      hasReachedPlacementPoint(
        { x: 60, y: 50 },
        null,
        { x: 10, y: 20, w: 100, h: 60 },
      ),
    ).toBe(true);
    expect(hasReachedPlacementPoint({ x: 60, y: 50 }, null, null)).toBe(false);
  });
});
