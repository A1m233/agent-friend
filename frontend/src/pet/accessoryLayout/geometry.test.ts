import { describe, expect, it } from "vitest";
import { computeAccessoryLayout, petCssToPhysical, physicalToPetCss, selectAccessoryWorkArea, type AccessoryLayout, type AccessorySide } from "./geometry";
import { actionBarButtonCount, actionBarMetrics, actionBarMinimumMetrics, actionBarPageCapacity } from "./actionBarMetrics";
import type { PetRect } from "@/pages/pet/petDisplayProfile";

const viewport = { x: 0, y: 0, w: 1920, h: 1040 };
const sizes = actionBarMetrics(12, 4);
const bubble = { w: 360, h: 240 };
function calculate(anchor: PetRect, options: Partial<Omit<Parameters<typeof computeAccessoryLayout>[0], "anchor">> = {}) {
  return computeAccessoryLayout({ anchor, workArea: viewport, sizes, minimumSizes: actionBarMinimumMetrics(12, 4), bubble, minimumBubbleSize: { w: 240, h: 64 }, gap: 8, ...options });
}
function assertInside(rect: PetRect, bounds: PetRect) {
  expect(rect.x).toBeGreaterThanOrEqual(bounds.x);
  expect(rect.y).toBeGreaterThanOrEqual(bounds.y);
  expect(rect.x + rect.w).toBeLessThanOrEqual(bounds.x + bounds.w);
  expect(rect.y + rect.h).toBeLessThanOrEqual(bounds.y + bounds.h);
}
function assertOrder(layout: AccessoryLayout, anchor: PetRect) {
  const bar = layout.actionBar;
  const b = layout.bubble;
  if (layout.side === "top") {
    if (b) expect(b.y + b.h).toBeLessThanOrEqual(bar.top - 8);
    expect(bar.top + bar.height).toBeLessThanOrEqual(anchor.y - 8);
  } else if (layout.side === "bottom") {
    expect(bar.top).toBeGreaterThanOrEqual(anchor.y + anchor.h + 8);
    if (b) expect(b.y).toBeGreaterThanOrEqual(bar.top + bar.height + 8);
  } else if (layout.side === "left") {
    if (b) expect(b.x + b.w).toBeLessThanOrEqual(bar.left - 8);
    expect(bar.left + bar.width).toBeLessThanOrEqual(anchor.x - 8);
  } else {
    expect(bar.left).toBeGreaterThanOrEqual(anchor.x + anchor.w + 8);
    if (b) expect(b.x).toBeGreaterThanOrEqual(bar.left + bar.width + 8);
  }
}

describe("pet accessory group", () => {
  it("keeps a top-center anchor horizontal below the pet", () => {
    const layout = calculate({ x: 880, y: 0, w: 160, h: 160 })!;
    expect(layout.side).toBe("bottom");
    expect(layout.actionBar.orientation).toBe("horizontal");
  });

  it("keeps a readable tall stack above the central pet instead of chasing full height", () => {
    const layout = calculate({ x: 880, y: 460, w: 160, h: 160 }, { bubble: { w: 360, h: 1800 } })!;
    expect(layout.side).toBe("top");
    expect(layout.actionBar.orientation).toBe("horizontal");
  });

  it("returns below the pet after leaving a side edge while still at the top", () => {
    const layout = calculate({ x: 880, y: 0, w: 160, h: 160 }, { previousSide: "left" })!;
    expect(layout.side).toBe("bottom");
    expect(layout.actionBar.orientation).toBe("horizontal");
  });

  it("puts the bubble beyond the bar instead of sharing the sprite's upper edge", () => {
    const anchor = { x: 880, y: 460, w: 160, h: 160 };
    const layout = calculate(anchor)!;
    expect(layout.side).toBe("top");
    assertOrder(layout, anchor);
  });

  it.each([0, 900, 1760])("keeps controls and notifications within the work area across edges at x=%s", (x) => {
    for (const y of [0, 450, 880]) {
      for (const size of [40, 160]) {
        const anchor = { x, y, w: size, h: size };
        const layout = calculate(anchor)!;
        expect(layout).not.toBeNull();
        assertOrder(layout, anchor);
        assertInside(layout.bubble!, viewport);
        assertInside({ x: layout.actionBar.left, y: layout.actionBar.top, w: layout.actionBar.width, h: layout.actionBar.height }, viewport);
        expect(layout.tooltipSide).toBe(layout.side);
        expect(layout.tooltipBoundary).not.toBeNull();
        assertInside(layout.tooltipBoundary!, viewport);
      }
    }
  });

  it("returns overhead after leaving the edge instead of retaining a lateral side forever", () => {
    const anchor = { x: 700, y: 500, w: 80, h: 100 };
    expect(calculate(anchor, { previousSide: "right" })?.side).toBe("top");
    expect(calculate(anchor, { previousSide: "left", bubble: { w: 1500, h: 200 } })?.side).toBe("top");
  });

  it.each([
    { x: 40, side: "right" },
    { x: 1800, side: "left" },
  ] as const)("switches to a vertical bar near the edge without requiring a visible bubble: $side", ({ x, side }) => {
    const anchor = { x, y: 500, w: 76, h: 96 };
    for (const content of [null, bubble]) {
      const layout = calculate(anchor, { bubble: content, previousSide: "top" })!;
      expect(layout.side).toBe(side);
      expect(layout.actionBar.orientation).toBe("vertical");
      expect(layout.tooltipSide).toBe(side);
      assertOrder(layout, anchor);
    }
  });

  it("keeps edge orientation within one gap of the boundary without sticking at the center", () => {
    const anchor = { x: 1740, y: 500, w: 40, h: 40 };
    expect(calculate(anchor)?.side).toBe("top");
    expect(calculate(anchor, { previousSide: "left" })?.side).toBe("left");
    expect(calculate({ ...anchor, x: 1720 }, { previousSide: "left" })?.side).toBe("top");
  });

  it("reproduces the user's right-monitor edge even when the previous top bar could be clamped", () => {
    const anchor = { x: 3963.9859, y: 682, w: 75.4872, h: 96 };
    const layout = calculate(anchor, { workArea: { x: 2048, y: 0, w: 2048, h: 1104 }, bubble: null, previousSide: "top" })!;
    expect(layout.side).toBe("left");
    expect(layout.actionBar.orientation).toBe("vertical");
    assertOrder(layout, anchor);
  });

  it.each([
    { x: 880, y: 460, side: "top", extent: 48 },
    { x: 880, y: 0, side: "bottom", extent: 48 },
    { x: 40, y: 500, side: "right", extent: 128 },
    { x: 1800, y: 500, side: "left", extent: 128 },
  ] as const)("reserves an outward tooltip lane separate from the native bubble: $side", ({ x, y, side, extent }) => {
    const layout = calculate({ x, y, w: 76, h: 96 })!;
    const bounds = layout.tooltipBoundary!;
    const b = layout.bubble!;
    expect(layout.side).toBe(side);
    expect(side === "top" || side === "bottom" ? bounds.h : bounds.w).toBeCloseTo(extent);
    if (side === "top") {
      expect(b.y + b.h).toBeLessThanOrEqual(bounds.y - 8);
      expect(bounds.y + bounds.h).toBeLessThanOrEqual(layout.actionBar.top - 8);
    } else if (side === "bottom") {
      expect(bounds.y).toBeGreaterThanOrEqual(layout.actionBar.top + layout.actionBar.height + 8);
      expect(b.y).toBeGreaterThanOrEqual(bounds.y + bounds.h + 8);
    } else if (side === "left") {
      expect(b.x + b.w).toBeLessThanOrEqual(bounds.x - 8);
      expect(bounds.x + bounds.w).toBeLessThanOrEqual(layout.actionBar.left - 8);
    } else {
      expect(bounds.x).toBeGreaterThanOrEqual(layout.actionBar.left + layout.actionBar.width + 8);
      expect(b.x).toBeGreaterThanOrEqual(bounds.x + bounds.w + 8);
    }
  });

  it("limits tall stacks without clamping them across the bar", () => {
    const anchor = { x: 400, y: 300, w: 100, h: 100 };
    const workArea = { x: 0, y: 0, w: 900, h: 600 };
    const layout = calculate(anchor, { workArea, bubble: { w: 360, h: 1800 } })!;
    assertInside(layout.bubble!, workArea);
    expect(layout.bubble!.h).toBeLessThan(1800);
    assertOrder(layout, anchor);
  });

  it("queues bubbles when there is no readable slot instead of covering the action bar", () => {
    const layout = calculate({ x: 70, y: 70, w: 160, h: 160 }, { workArea: { x: 0, y: 0, w: 300, h: 300 } })!;
    expect(layout.actionBar).toBeDefined();
    expect(layout.bubble).toBeNull();
  });

  it("rejects unavailable and invalid geometry", () => {
    expect(calculate({ x: NaN, y: 1, w: 1, h: 1 })).toBeNull();
    expect(calculate({ x: 1, y: 1, w: 1, h: 1 }, { workArea: { x: 0, y: 0, w: 0, h: 0 } })).toBeNull();
  });
});

const regions: Array<{ region: string; side: AccessorySide; position: (size: number) => PetRect }> = [
  { region: "center", side: "top", position: (s) => ({ x: (1920 - s) / 2, y: (1040 - s) / 2, w: s, h: s }) },
  { region: "top", side: "bottom", position: (s) => ({ x: (1920 - s) / 2, y: 0, w: s, h: s }) },
  { region: "bottom", side: "top", position: (s) => ({ x: (1920 - s) / 2, y: 1040 - s, w: s, h: s }) },
  { region: "left", side: "right", position: (s) => ({ x: 0, y: (1040 - s) / 2, w: s, h: s }) },
  { region: "right", side: "left", position: (s) => ({ x: 1920 - s, y: (1040 - s) / 2, w: s, h: s }) },
  { region: "top-left", side: "right", position: (s) => ({ x: 0, y: 0, w: s, h: s }) },
  { region: "top-right", side: "left", position: (s) => ({ x: 1920 - s, y: 0, w: s, h: s }) },
  { region: "bottom-left", side: "right", position: (s) => ({ x: 0, y: 1040 - s, w: s, h: s }) },
  { region: "bottom-right", side: "left", position: (s) => ({ x: 1920 - s, y: 1040 - s, w: s, h: s }) },
];
const contents = [
  { contentName: "hidden", content: null },
  { contentName: "short", content: { w: 360, h: 96 } },
  { contentName: "tall", content: { w: 360, h: 1800 } },
];

function assertDirection(layout: AccessoryLayout | null, side: AccessorySide) {
  expect(layout).not.toBeNull();
  expect(layout!.side).toBe(side);
  expect(layout!.actionBar.orientation).toBe(side === "top" || side === "bottom" ? "horizontal" : "vertical");
}

describe("region directions preserved for every avatar and notification state", () => {
  it.each(regions.flatMap((region) => [40, 160].flatMap((avatarSize) => contents.map((content) => ({ ...region, avatarSize, ...content })))))(
    "$region / avatar $avatarSize / $contentName keeps $side", ({ position, side, avatarSize, content }) => {
      const anchor = position(avatarSize);
      const layout = calculate(anchor, { bubble: content });
      assertDirection(layout, side);
      const { actionBar, tooltipBoundary } = layout!;
      assertInside({ x: actionBar.left, y: actionBar.top, w: actionBar.width, h: actionBar.height }, viewport);
      expect(tooltipBoundary).not.toBeNull();
      assertInside(tooltipBoundary!, viewport);
      expect(layout!.tooltipSide).toBe(side);
      assertOrder(layout!, anchor);
      if (content === null) {
        expect(layout!.bubble).toBeNull();
      } else {
        expect(layout!.bubble).not.toBeNull();
        assertInside(layout!.bubble!, viewport);
        expect(layout!.bubble!.w).toBeGreaterThanOrEqual(240);
        expect(layout!.bubble!.h).toBeGreaterThanOrEqual(64);
        expect(layout!.bubble!.w).toBeLessThanOrEqual(content.w);
        expect(layout!.bubble!.h).toBeLessThanOrEqual(content.h);
      }
    });

  it.each([40, 160])("moves top to bottom and back without retaining the old side: avatar %s", (avatarSize) => {
    for (const { content } of contents) {
      let previousSide: AccessorySide | undefined;
      for (const regionName of ["top", "bottom", "top", "center"]) {
        const region = regions.find(({ region }) => region === regionName)!;
        const layout = calculate(region.position(avatarSize), { bubble: content, previousSide });
        assertDirection(layout, region.side);
        previousSide = layout!.side;
      }
    }
  });

  it.each(["top-left", "top-right"])("leaves %s toward top-center as bottom, then center as top", (cornerName) => {
    const corner = regions.find(({ region }) => region === cornerName)!;
    for (const { content } of contents) {
      const first = calculate(corner.position(160), { bubble: content });
      assertDirection(first, corner.side);
      const above = calculate(regions[1].position(160), { bubble: content, previousSide: first!.side });
      assertDirection(above, "bottom");
      const center = calculate(regions[0].position(160), { bubble: content, previousSide: above!.side });
      assertDirection(center, "top");
    }
  });

  it.each(regions)("hidden to short to tall to hidden keeps the $region region direction", ({ position, side }) => {
    let previousSide: AccessorySide | undefined;
    for (const content of [null, { w: 360, h: 96 }, { w: 360, h: 1800 }, null]) {
      const layout = calculate(position(160), { bubble: content, previousSide });
      assertDirection(layout, side);
      expect(layout!.bubble === null).toBe(content === null);
      previousSide = layout!.side;
    }
  });

  it("limits an oversized width and height in the first readable direction", () => {
    const anchor = regions[0].position(160);
    const layout = calculate(anchor, { bubble: { w: 4000, h: 4000 } })!;
    assertDirection(layout, "top");
    expect(layout.bubble).not.toBeNull();
    assertInside(layout.bubble!, viewport);
    assertOrder(layout, anchor);
  });

  it("uses the next regional direction only when the first cannot hold a readable bubble", () => {
    const anchor = { x: 880, y: 120, w: 160, h: 160 };
    assertDirection(calculate(anchor, { bubble: null }), "top");
    const shown = calculate(anchor, { bubble: { w: 360, h: 96 } })!;
    assertDirection(shown, "bottom");
    assertOrder(shown, anchor);
  });
});

describe("legacy action bar placement contracts", () => {
  it.each([
    { y: 59, side: "bottom", top: 107 },
    { y: 60, side: "top", top: 8 },
    { y: 61, side: "top", top: 9 },
  ] as const)("preserves the old bar-margin fallback at y=$y when no direction has a readable tooltip", ({ y, side, top }) => {
    const layout = calculate({ x: 48, y, w: 40, h: 40 }, {
      workArea: { x: 0, y: 0, w: 136, h: 160 }, bubble: null,
    })!;
    assertDirection(layout, side);
    expect(layout.actionBar.top).toBe(top);
    expect(layout.tooltipBoundary).toBeNull();
  });

  it.each([
    { y: 95, side: "bottom", top: 263 },
    { y: 96, side: "top", top: 44 },
    { y: 97, side: "top", top: 45 },
  ] as const)("prefers a readable horizontal tooltip over bar-only placement at y=$y", ({ y, side, top }) => {
    const layout = calculate({ x: 880, y, w: 160, h: 160 }, { bubble: null })!;
    assertDirection(layout, side);
    expect(layout.actionBar.top).toBe(top);
    expect(layout.tooltipBoundary).not.toBeNull();
  });

  it.each([
    { x: 1920, y: 320 },
    { x: -2560, y: -200 },
  ])("uses offset work-area edges at $x,$y for top and side placement", ({ x, y }) => {
    const workArea = { x, y, w: 1920, h: 1080 };
    const topAnchor = { x: x + 680, y: y + 10, w: 120, h: 120 };
    const layout = calculate(topAnchor, { workArea, bubble: null })!;
    assertDirection(layout, "bottom");
    expect(layout.actionBar.top).toBe(y + 138);
    expect(layout.actionBar.left).toBe(x + 590);
    assertDirection(calculate({ x: x + 1860, y: y + 420, w: 80, h: 100 }, { workArea, bubble: null }), "left");
  });

  it.each([
    { x: 600, side: "left", left: 548 },
    { x: 240, side: "right", left: 408 },
  ] as const)("switches a naturally overflowing horizontal bar before any clamping: $side", ({ x, side, left }) => {
    const layout = calculate({ x, y: 300, w: 160, h: 160 }, {
      workArea: { x: 0, y: 0, w: 1000, h: 800 }, bubble: null,
      sizes: { horizontal: { w: 640, h: 44 }, vertical: { w: 44, h: 224 } },
      minimumSizes: { horizontal: { w: 120, h: 44 }, vertical: { w: 44, h: 116 } },
    })!;
    assertDirection(layout, side);
    expect(layout.actionBar.left).toBe(left);
  });

  it("clamps the old bottom-left vertical bar on its cross axis", () => {
    const layout = calculate({ x: -30, y: 760, w: 100, h: 120 }, {
      workArea: { x: 0, y: 0, w: 1000, h: 800 }, bubble: null,
      sizes: { horizontal: { w: 260, h: 44 }, vertical: { w: 44, h: 224 } },
      minimumSizes: { horizontal: { w: 120, h: 44 }, vertical: { w: 44, h: 116 } },
    })!;
    assertDirection(layout, "right");
    expect(layout.actionBar.left).toBe(78);
    expect(layout.actionBar.top).toBe(568);
  });

  it("keeps horizontal alignment centered on a smaller visible avatar", () => {
    const layout = calculate({ x: 500, y: 400, w: 40, h: 40 }, { bubble: null })!;
    assertDirection(layout, "top");
    expect(layout.actionBar.left + layout.actionBar.width / 2).toBe(520);
  });

  it("mirrors the right-edge hysteresis at the left edge", () => {
    const anchor = { x: 140, y: 500, w: 40, h: 40 };
    assertDirection(calculate(anchor, { bubble: null }), "top");
    assertDirection(calculate(anchor, { bubble: null, previousSide: "right" }), "right");
    assertDirection(calculate({ ...anchor, x: 160 }, { bubble: null, previousSide: "right" }), "top");
  });

  it("recovers horizontal layout after action count no longer causes natural overflow", () => {
    const anchor = { x: 130, y: 400, w: 40, h: 40 };
    const wide = calculate(anchor, { bubble: null, sizes: actionBarMetrics(12, 4) });
    assertDirection(wide, "right");
    const narrow = calculate(anchor, { bubble: null, sizes: actionBarMetrics(6, 4), previousSide: wide!.side });
    assertDirection(narrow, "top");
  });
});

describe("minimum readable and operable accessory spaces", () => {
  it.each([
    { width: 136, visible: true },
    { width: 135, visible: false },
  ])("requires one horizontal action and paging controls at width $width", ({ width, visible }) => {
    const layout = calculate({ x: (width - 40) / 2, y: 280, w: 40, h: 40 }, {
      workArea: { x: 0, y: 0, w: width, h: 600 }, bubble: null,
    });
    if (!visible) { expect(layout).toBeNull(); return; }
    assertDirection(layout, "top");
    expect(layout!.actionBar.width).toBe(120);
    expect(layout!.actionBar.height).toBe(44);
    expect(actionBarPageCapacity(12, layout!.actionBar.width, false, 4)).toBe(1);
  });

  it.each([
    { height: 132, visible: true },
    { height: 131, visible: false },
  ])("requires one vertical action and paging controls at height $height", ({ height, visible }) => {
    const layout = calculate({ x: 20, y: (height - 40) / 2, w: 40, h: 40 }, {
      workArea: { x: 0, y: 0, w: 300, h: height }, bubble: null,
    });
    if (!visible) { expect(layout).toBeNull(); return; }
    assertDirection(layout, "right");
    expect(layout!.actionBar.width).toBe(48);
    expect(layout!.actionBar.height).toBe(116);
    expect(actionBarPageCapacity(12, layout!.actionBar.height, true, 4)).toBe(1);
  });

  it.each([
    { y: 95, tooltipHeight: null },
    { y: 96, tooltipHeight: 28 },
  ])("keeps the bar operable when its only tooltip corridor is at the readability boundary: y=$y", ({ y, tooltipHeight }) => {
    const layout = calculate({ x: 48, y, w: 40, h: 40 }, {
      workArea: { x: 0, y: 0, w: 136, h: 160 }, bubble: null,
    })!;
    assertDirection(layout, "top");
    expect(layout.actionBar.width).toBe(120);
    if (tooltipHeight === null) {
      expect(layout.tooltipBoundary).toBeNull();
    } else {
      expect(layout.tooltipBoundary?.w).toBe(120);
      expect(layout.tooltipBoundary?.h).toBe(tooltipHeight);
    }
  });

  it("queues unreadable bubbles while retaining the first regional bar with a readable tooltip", () => {
    const anchor = { x: 20, y: 70, w: 40, h: 40 };
    const workArea = { x: 0, y: 0, w: 300, h: 210 };
    const layout = calculate(anchor, { workArea })!;
    assertDirection(layout, "right");
    expect(layout.bubble).toBeNull();
    expect(layout.tooltipBoundary).not.toBeNull();
    assertInside(layout.tooltipBoundary!, workArea);
  });

  it("restores the original region direction after a constrained viewport expands", () => {
    const anchor = { x: 48, y: 280, w: 40, h: 40 };
    const narrow = calculate(anchor, { workArea: { x: 0, y: 0, w: 136, h: 600 }, bubble: null })!;
    assertDirection(narrow, "top");
    const wide = calculate(anchor, { bubble: null, previousSide: narrow.side });
    assertDirection(wide, "right");
  });
});

describe("native accessory coordinates", () => {
  it.each([1, 1.25, 1.75, 2])("preserves regional directions through physical work-area offsets at DPR %s", (dpr) => {
    const origin = { x: -2560, y: 320 };
    const context = { generation: 1, origin, monitors: [{
      x: origin.x, y: origin.y, width: 1920 * dpr, height: 1080 * dpr, scaleMilli: 1250,
      workArea: { x: origin.x, y: origin.y, width: 1920 * dpr, height: 1040 * dpr },
    }] };
    for (const region of regions) {
      const expectedAnchor = region.position(160);
      const anchor = physicalToPetCss(petCssToPhysical(expectedAnchor, origin, dpr), origin, dpr);
      const workArea = selectAccessoryWorkArea(anchor, context, dpr)!;
      expect(workArea).toEqual(viewport);
      const layout = calculate(anchor, { workArea, bubble: null });
      assertDirection(layout, region.side);
    }
  });

  it("uses the overlay DPR even when the selected monitor has a different scale", () => {
    const context = { generation: 4, origin: { x: -2560, y: -200 }, monitors: [
      { x: -2560, y: -200, width: 2560, height: 1440, scaleMilli: 1250, workArea: { x: -2560, y: -200, width: 2560, height: 1400 } },
      { x: 0, y: 0, width: 1920, height: 1080, scaleMilli: 1750, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
    ] };
    const dpr = 1.75;
    const anchor = physicalToPetCss({ x: -2000, y: 400, width: 280, height: 280 }, context.origin, dpr);
    const area = selectAccessoryWorkArea(anchor, context, dpr)!;
    expect(area.w).toBeCloseTo(2560 / 1.75);
    expect(area.h).toBeCloseTo(1400 / 1.75);
    const physical = petCssToPhysical(anchor, context.origin, dpr);
    expect(physical).toEqual({ x: -2000, y: 400, width: 280, height: 280 });
  });

  it("chooses the containing full monitor even when the sprite is over its taskbar", () => {
    const context = { generation: 1, origin: { x: 0, y: 0 }, monitors: [
      { x: 0, y: 0, width: 1000, height: 800, scaleMilli: 1000, workArea: { x: 0, y: 0, width: 1000, height: 740 } },
      { x: 0, y: 800, width: 1000, height: 800, scaleMilli: 1000, workArea: { x: 0, y: 800, width: 1000, height: 800 } },
    ] };
    expect(selectAccessoryWorkArea({ x: 400, y: 750, w: 50, h: 40 }, context, 1)).toEqual({ x: 0, y: 0, w: 1000, h: 740 });
  });

  it("preserves current action counts and carousel dimensions while hidden", () => {
    expect(actionBarButtonCount(false, false)).toBe(7);
    expect(actionBarButtonCount(true, true)).toBe(14);
    expect(actionBarButtonCount(true, false)).toBe(13);
    expect(actionBarButtonCount(false, true)).toBe(8);
    expect(actionBarMetrics(6, 4)).toEqual({ horizontal: { w: 228, h: 44 }, vertical: { w: 48, h: 224 } });
    expect(actionBarMetrics(12, 4)).toEqual({ horizontal: { w: 300, h: 44 }, vertical: { w: 48, h: 296 } });
    expect(actionBarMinimumMetrics(1, 4)).toEqual({ horizontal: { w: 48, h: 44 }, vertical: { w: 48, h: 44 } });
    expect(actionBarMinimumMetrics(2, 4)).toEqual({ horizontal: { w: 84, h: 44 }, vertical: { w: 48, h: 80 } });
    expect(actionBarMinimumMetrics(12, 4)).toEqual({ horizontal: { w: 120, h: 44 }, vertical: { w: 48, h: 116 } });
    expect(actionBarPageCapacity(6, 228, false, 4)).toBe(6);
    expect(actionBarPageCapacity(6, 200, false, 4)).toBe(3);
    expect(actionBarPageCapacity(12, 296, true, 4)).toBe(6);
    expect(actionBarPageCapacity(12, 200, true, 4)).toBe(3);
  });
});
