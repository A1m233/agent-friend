import { describe, expect, it } from "vitest";
import { shouldRenderActionBar } from "./actionBarVisibility";

const SPRITE = { x: 100, y: 200, w: 80, h: 120 };
const VIEWPORT = { x: 0, y: 0, w: 1920, h: 1080 };

describe("shouldRenderActionBar", () => {
  it("removes the ActionBar subtree whenever sticky visibility is false", () => {
    expect(
      shouldRenderActionBar({
        visible: false,
        spriteScreen: SPRITE,
        actionBarAnchor: SPRITE,
        viewport: VIEWPORT,
      }),
    ).toBe(false);
  });

  it("renders only when visibility and geometry are both valid", () => {
    expect(
      shouldRenderActionBar({
        visible: true,
        spriteScreen: SPRITE,
        actionBarAnchor: SPRITE,
        viewport: VIEWPORT,
      }),
    ).toBe(true);
    expect(
      shouldRenderActionBar({
        visible: true,
        spriteScreen: null,
        actionBarAnchor: null,
        viewport: VIEWPORT,
      }),
    ).toBe(false);
    expect(
      shouldRenderActionBar({
        visible: true,
        spriteScreen: SPRITE,
        actionBarAnchor: { ...SPRITE, x: Number.NaN },
        viewport: VIEWPORT,
      }),
    ).toBe(false);
  });
});
