import { describe, expect, it } from "vitest";
import { computeActionBarLayout } from "./actionBarLayout";

describe("actionBarLayout · orientation is driven by anchor position", () => {
  const viewport = { x: 0, y: 0, w: 1000, h: 800 };
  const sizes = {
    horizontal: { w: 260, h: 44 },
    vertical: { w: 44, h: 224 },
  };

  it("uses horizontal layout away from screen edges", () => {
    const layout = computeActionBarLayout(
      { x: 420, y: 300, w: 160, h: 160 },
      sizes,
      viewport,
    );

    expect(layout.orientation).toBe("horizontal");
    expect(layout.width).toBe(260);
    expect(layout.height).toBe(44);
  });

  it("uses vertical layout near the left edge", () => {
    const layout = computeActionBarLayout(
      { x: -30, y: 300, w: 100, h: 120 },
      sizes,
      viewport,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.left).toBe(78);
    expect(layout.top).toBe(248);
  });

  it("uses vertical layout near the right edge", () => {
    const layout = computeActionBarLayout(
      { x: 930, y: 300, w: 100, h: 120 },
      sizes,
      viewport,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.left).toBe(878);
  });

  it("uses vertical layout when the full horizontal chip would overflow right", () => {
    const layout = computeActionBarLayout(
      { x: 600, y: 300, w: 160, h: 160 },
      {
        horizontal: { w: 640, h: 44 },
        vertical: { w: 44, h: 224 },
      },
      viewport,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.left).toBe(548);
  });

  it("uses vertical layout when the full horizontal chip would overflow left", () => {
    const layout = computeActionBarLayout(
      { x: 240, y: 300, w: 160, h: 160 },
      {
        horizontal: { w: 640, h: 44 },
        vertical: { w: 44, h: 224 },
      },
      viewport,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.left).toBe(408);
  });

  it("clamps vertical layout to viewport", () => {
    const layout = computeActionBarLayout(
      { x: -30, y: 760, w: 100, h: 120 },
      sizes,
      viewport,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.top).toBe(568);
  });

  it("treats the selected monitor viewport as the edge inside a multi-monitor overlay", () => {
    const leftMonitor = { x: 0, y: 0, w: 1920, h: 1080 };
    const layout = computeActionBarLayout(
      { x: 1860, y: 420, w: 80, h: 100 },
      sizes,
      leftMonitor,
    );

    expect(layout.orientation).toBe("vertical");
    expect(layout.left).toBe(1808);
  });

  it("uses viewport origin when placing bars on an offset monitor", () => {
    const lowerMonitor = { x: 1920, y: 320, w: 1920, h: 1080 };
    const layout = computeActionBarLayout(
      { x: 2600, y: 330, w: 120, h: 120 },
      sizes,
      lowerMonitor,
    );

    expect(layout.orientation).toBe("horizontal");
    expect(layout.top).toBe(458);
  });
});
