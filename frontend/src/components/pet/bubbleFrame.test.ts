import { describe, expect, it } from "vitest";
import type { BubblePresentationState, PhysicalRect } from "@/pet/accessoryLayout/protocol";
import { bubbleFrame } from "./bubbleFrame";

function state(changes: Partial<BubblePresentationState> = {}): BubblePresentationState {
  return {
    stateRevision: 1, layoutOwner: 1, visible: true, applied: true,
    measurementRevision: 1, layoutRevision: 1, widthCss: 400, heightCss: 400,
    hostRectPhysical: { x: 100, y: 100, width: 400, height: 400 },
    targetRectPhysical: { x: 100, y: 100, width: 400, height: 200 },
    scaleFactor: 1, retaining: true, carrierPlan: null, ...changes,
  };
}

describe("bubbleFrame", () => {
  it("anchors a top-side shrinking stack to the bottom of its retained carrier", () => {
    expect(bubbleFrame(state({ targetRectPhysical: { x: 100, y: 300, width: 400, height: 200 } })))
      .toEqual({ width: 400, height: 200, x: 0, y: 1 });
  });

  it("anchors a bottom-side shrinking stack to the top of its carrier", () => {
    expect(bubbleFrame(state())).toEqual({ width: 400, height: 200, x: 0, y: 0 });
  });

  it("uses the actual side-clamped target instead of assuming vertical centering", () => {
    expect(bubbleFrame(state({ targetRectPhysical: { x: 220, y: 150, width: 240, height: 200 } })))
      .toEqual({ width: 240, height: 200, x: 0.75, y: 0.25 });
  });

  it.each([-80, 500])("clamps an outlying target offset %s to the host's usable ratio", (offset) => {
    const frame = bubbleFrame(state({ targetRectPhysical: { x: 100 + offset, y: 100 + offset, width: 200, height: 200 } }))!;
    expect(frame.x).toBe(offset < 0 ? 0 : 1);
    expect(frame.y).toBe(offset < 0 ? 0 : 1);
  });

  it("has no offset when target and carrier are the same size", () => {
    const rect = { x: -1200, y: 320, width: 360, height: 240 };
    expect(bubbleFrame(state({ hostRectPhysical: rect, targetRectPhysical: rect })))
      .toEqual({ width: 360, height: 240, x: 0, y: 0 });
  });

  it.each([1, 1.25, 1.75, 2])("preserves the global target through native cropping at bubble DPR %s without a JS reset", (scaleFactor) => {
    const host = { x: -1300, y: 160, width: 600, height: 800 };
    const target = { x: -1260, y: 600, width: 450, height: 360 };
    const frame = bubbleFrame(state({ hostRectPhysical: host, targetRectPhysical: target, scaleFactor }))!;
    // Evaluate the existing CSS calc against the old and new native viewport;
    // no new snapshot or JS style write is needed at the crop boundary.
    const contentOrigin = (carrier: PhysicalRect) => ({
      x: carrier.x + (16 + frame.x * (carrier.width / scaleFactor - frame.width)) * scaleFactor,
      y: carrier.y + (16 + frame.y * (carrier.height / scaleFactor - frame.height)) * scaleFactor,
    });
    expect(contentOrigin(host).x).toBeCloseTo(target.x + 16 * scaleFactor);
    expect(contentOrigin(host).y).toBeCloseTo(target.y + 16 * scaleFactor);
    expect(contentOrigin(target)).toEqual(contentOrigin(host));
    expect(frame.width).toBe(450 / scaleFactor);
    expect(frame.height).toBe(360 / scaleFactor);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])("rejects invalid scale %s", (scaleFactor) => {
    expect(bubbleFrame(state({ scaleFactor }))).toBeNull();
  });

  it("requires both native rectangles", () => {
    expect(bubbleFrame(state({ hostRectPhysical: null }))).toBeNull();
    expect(bubbleFrame(state({ targetRectPhysical: null }))).toBeNull();
  });
});
