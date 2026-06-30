import { describe, expect, it, vi } from "vitest";
import { boundsToRegion, fitSpine38ToStage } from "./spine38Fit";

function makeFitHarness(bounds: { x: number; y: number; width: number; height: number }) {
  const slotScaleSet = vi.fn();
  const slotPositionSet = vi.fn();
  const spinePositionSet = vi.fn();
  const update = vi.fn();

  return {
    app: {
      screen: { width: 320, height: 320 },
    },
    slot: {
      scale: { set: slotScaleSet },
      position: { set: slotPositionSet },
    },
    spine: {
      position: { set: spinePositionSet },
      update,
      getLocalBounds: () => bounds,
    },
    slotScaleSet,
    slotPositionSet,
    spinePositionSet,
    update,
  };
}

function makeFitHarnessForStage(
  screen: { width: number; height: number },
  bounds: { x: number; y: number; width: number; height: number },
) {
  return {
    ...makeFitHarness(bounds),
    app: {
      screen,
    },
  };
}

describe("fitSpine38ToStage", () => {
  it("treats null bounds as unavailable", () => {
    expect(boundsToRegion(null)).toBeNull();
    expect(boundsToRegion(undefined)).toBeNull();
  });

  it("uses the compact Ark chibi default fit", () => {
    const harness = makeFitHarness({ x: -10, y: -20, width: 100, height: 200 });

    fitSpine38ToStage(harness.app as never, harness.slot as never, harness.spine as never);

    expect(harness.slotPositionSet.mock.lastCall?.[0]).toBe(160);
    expect(harness.slotPositionSet.mock.lastCall?.[1]).toBeCloseTo(275.2);
    expect(harness.update).toHaveBeenCalledWith(0);
    expect(harness.slotScaleSet.mock.lastCall?.[0]).toBeCloseTo(1.088);
    expect(harness.spinePositionSet).toHaveBeenLastCalledWith(-40, -180);
  });

  it("caps an over-large height fit with top padding", () => {
    const harness = makeFitHarness({ x: 0, y: 0, width: 100, height: 200 });

    fitSpine38ToStage(harness.app as never, harness.slot as never, harness.spine as never, {
      fitWidthRatio: 1,
      fitHeightRatio: 0.82,
      slotYRatio: 0.86,
      topPaddingRatio: 0.2,
    });

    expect(harness.slotScaleSet.mock.lastCall?.[0]).toBeCloseTo(1.056);
  });

  it("uses a configured logical fit box instead of scaling to the full desktop overlay", () => {
    const harness = makeFitHarnessForStage(
      { width: 1728, height: 1117 },
      { x: -10, y: -20, width: 100, height: 200 },
    );

    fitSpine38ToStage(harness.app as never, harness.slot as never, harness.spine as never, {
      fitWidthPx: 320,
      fitHeightPx: 320,
      fitWidthRatio: 0.62,
      fitHeightRatio: 0.68,
      slotYRatio: 0.86,
      topPaddingRatio: 0.16,
    });

    expect(harness.slotPositionSet.mock.lastCall?.[0]).toBe(864);
    expect(harness.slotPositionSet.mock.lastCall?.[1]).toBeCloseTo(960.62);
    expect(harness.slotScaleSet.mock.lastCall?.[0]).toBeCloseTo(1.088);
  });

  it("keeps wide Ark skeletons height-consistent within a wider safety cap", () => {
    const harness = makeFitHarness({
      x: -629.19,
      y: -496.98,
      width: 887.26,
      height: 499.03,
    });

    fitSpine38ToStage(harness.app as never, harness.slot as never, harness.spine as never, {
      fitWidthPx: 320,
      fitHeightPx: 320,
      fitWidthRatio: 1.35,
      fitHeightRatio: 0.68,
      slotYRatio: 0.86,
      topPaddingRatio: 0.16,
    }, {
      x: -228.2,
      y: -469.6,
      w: 459.5,
      h: 469.6,
    });

    const scale = harness.slotScaleSet.mock.lastCall?.[0] ?? 0;
    expect(scale).toBeCloseTo(0.463);
    expect(469.6 * scale).toBeCloseTo(217.6);
    expect(459.5 * scale).toBeLessThan(432);
    expect(harness.spinePositionSet.mock.lastCall?.[0]).toBeCloseTo(-1.55);
    expect(harness.spinePositionSet.mock.lastCall?.[1]).toBeCloseTo(0);
  });

  it("positions Spine within the target monitor viewport instead of a multi-screen overlay union", () => {
    const harness = makeFitHarnessForStage(
      { width: 1920, height: 1928 },
      { x: -10, y: -20, width: 100, height: 200 },
    );

    fitSpine38ToStage(
      harness.app as never,
      harness.slot as never,
      harness.spine as never,
      {
        fitWidthPx: 320,
        fitHeightPx: 320,
        fitWidthRatio: 1.35,
        fitHeightRatio: 0.68,
        slotYRatio: 0.86,
        topPaddingRatio: 0.16,
      },
      { x: -10, y: -20, w: 100, h: 200 },
      { x: 0, y: 0, w: 1920, h: 1080 },
    );

    expect(harness.slotPositionSet).toHaveBeenLastCalledWith(960, 928.8);
  });
});
