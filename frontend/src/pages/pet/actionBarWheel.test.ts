import { describe, expect, it } from "vitest";
import { actionBarWheelDistance, createActionBarWheelGesture } from "./actionBarWheel";

describe("ActionBar continuous wheel scrolling", () => {
  it.each([
    [0, 0.25, 0, 0.25], [-11, 3, 0, -11], [1, -12, 0, -12],
    [0, 3, 1, 48], [-1, 0, 2, -220], [0, 0, 0, 0], [0, NaN, 0, 0],
  ])("maps (%s, %s) in mode %s to %s CSS pixels", (deltaX, deltaY, deltaMode, expected) => {
    expect(actionBarWheelDistance({ deltaX, deltaY, deltaMode, ctrlKey: false }, 16, 220)).toBe(expected);
  });

  it("leaves pinch zoom to the WebView", () => {
    expect(actionBarWheelDistance({ deltaX: 0, deltaY: 120, deltaMode: 0, ctrlKey: true }, 16, 220)).toBe(0);
  });
});

describe("ActionBar wheel gestures", () => {
  const notch = { deltaMode: 0, deltaX: 0, deltaY: 100, wheelDeltaY: -120 };

  it("pages once per burst, again after a pause or on reversal", () => {
    const gesture = createActionBarWheelGesture();
    expect(gesture(notch, 0, 1)).toBe("page");
    expect(gesture(notch, 50, 1)).toBe("ignore");
    expect(gesture({ ...notch, deltaY: -100, wheelDeltaY: 120 }, 80, 1)).toBe("page");
    expect(gesture({ ...notch, deltaY: -100, wheelDeltaY: 120 }, 240, 1)).toBe("page");
  });

  it("keeps a swipe continuous even if a faster sample resembles a notch", () => {
    const gesture = createActionBarWheelGesture();
    expect(gesture({ ...notch, deltaY: 2.5 }, 0, 1)).toBe("scroll");
    expect(gesture(notch, 5, 1)).toBe("scroll");
    expect(gesture({ ...notch, deltaX: 5 }, 14, 1)).toBe("scroll");
    expect(gesture(notch, 200, 1)).toBe("page");
  });

  it.each([
    [{ deltaMode: 0, deltaX: 80, deltaY: 0, wheelDeltaX: -96 }, 1.25, "page"],
    [{ deltaMode: 1, deltaX: 0, deltaY: 3 }, 1, "page"],
    [{ deltaMode: 2, deltaX: 1, deltaY: 0 }, 1, "page"],
    [{ deltaMode: 0, deltaX: 0, deltaY: 120 }, 1, "page"],
    [{ deltaMode: 0, deltaX: 1, deltaY: 120, wheelDeltaY: -120 }, 1, "scroll"],
    [{ deltaMode: 0, deltaX: 0, deltaY: 75, wheelDeltaY: -75 }, 1, "scroll"],
  ] as const)("classifies %o at scale %s as %s", (event, ratio, expected) => {
    expect(createActionBarWheelGesture()(event, 0, ratio)).toBe(expected);
  });
});
