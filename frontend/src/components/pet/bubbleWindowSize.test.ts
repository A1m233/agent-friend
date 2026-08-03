import { describe, expect, it } from "vitest";
import {
  calculateBubbleStackWindowSize,
  calculateBubbleWindowSize,
  parseCssPixels,
} from "./bubbleWindowSize";

describe("calculateBubbleWindowSize", () => {
  it("includes the bubble border box and all frame padding", () => {
    expect(
      calculateBubbleWindowSize(
        { width: 217, height: 440 },
        { top: 4, right: 4, bottom: 4, left: 4 },
      ),
    ).toEqual({ width: 225, height: 448 });
  });

  it("rounds fractional logical pixels up so the native viewport cannot clip", () => {
    expect(
      calculateBubbleWindowSize(
        { width: 216.8, height: 137.6 },
        { top: 4, right: 4, bottom: 4, left: 4 },
      ),
    ).toEqual({ width: 225, height: 146 });
  });
});

describe("calculateBubbleStackWindowSize", () => {
  it("uses the furthest Sonner target offset and reserves a 16px shadow frame", () => {
    expect(
      calculateBubbleStackWindowSize([
        { height: 68, offset: 0 },
        { height: 76, offset: 82 },
      ]),
    ).toEqual({ width: 360, height: 190 });
  });

  it("returns the native window width even before a toast contributes height", () => {
    expect(calculateBubbleStackWindowSize([])).toEqual({ width: 360, height: 32 });
  });
});

describe("parseCssPixels", () => {
  it("parses computed pixel values and safely falls back for invalid input", () => {
    expect(parseCssPixels("4px")).toBe(4);
    expect(parseCssPixels("0.8px")).toBe(0.8);
    expect(parseCssPixels("normal")).toBe(0);
  });
});
