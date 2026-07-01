import { describe, expect, it } from "vitest";
import {
  PET_EDGE_RECALL_SCALE,
  computeEdgeRecallPlacement,
  resolvePetDisplayProfile,
  type PetRect,
  type PetViewport,
} from "./petDisplayProfile";

describe("petDisplayProfile · edge recall placement", () => {
  const viewport: PetViewport = { x: 0, y: 0, w: 1000, h: 800 };
  const rect: PetRect = { x: 420, y: 300, w: 200, h: 300 };

  it("normal mode keeps scale 1 and does not force placement", () => {
    const profile = resolvePetDisplayProfile("normal", rect, viewport);

    expect(profile).toEqual({
      mode: "normal",
      scale: 1,
      placementPoint: null,
      edge: null,
    });
  });

  it("normal mode clamps center when restoring from edge recall", () => {
    const edgeRect: PetRect = { x: -30, y: 320, w: 100, h: 150 };

    const profile = resolvePetDisplayProfile("normal", edgeRect, viewport, {
      previousMode: "edgeRecall",
    });

    expect(profile.mode).toBe("normal");
    expect(profile.scale).toBe(1);
    expect(profile.placementPoint).toEqual({ x: 108, y: 395 });
  });

  it("edgeRecall mode scales avatar and computes a recall point", () => {
    const profile = resolvePetDisplayProfile("edgeRecall", rect, viewport, {
      previousMode: "normal",
    });

    expect(profile.mode).toBe("edgeRecall");
    expect(profile.scale).toBe(PET_EDGE_RECALL_SCALE);
    expect(profile.placementPoint).not.toBeNull();
    expect(profile.edge).not.toBeNull();
  });

  it("uses right edge for persisted edgeRecall cold start without previous mode", () => {
    const profile = resolvePetDisplayProfile("edgeRecall", rect, viewport, {
      previousMode: null,
    });

    expect(profile.edge).toBe("right");
    expect(profile.placementPoint).toEqual({ x: 980, y: 450 });
  });

  it("places left edge recall partly off screen with a visible recall area", () => {
    const { point, edge } = computeEdgeRecallPlacement(rect, viewport, { edge: "left" });

    expect(edge).toBe("left");
    expect(point.x).toBe(20);
    expect(point.y).toBe(450);
  });

  it("places right edge recall symmetrically", () => {
    const { point, edge } = computeEdgeRecallPlacement(rect, viewport, { edge: "right" });

    expect(edge).toBe("right");
    expect(point.x).toBe(980);
    expect(point.y).toBe(450);
  });

  it("clamps the free axis inside the viewport", () => {
    const lowRect: PetRect = { x: 420, y: 760, w: 200, h: 300 };

    const { point } = computeEdgeRecallPlacement(lowRect, viewport, { edge: "left" });

    expect(point.y).toBe(717);
  });

  it("chooses only between left and right edges", () => {
    const topRect: PetRect = { x: 420, y: 0, w: 200, h: 300 };

    const { edge, point } = computeEdgeRecallPlacement(topRect, viewport);

    expect(edge).toBe("right");
    expect(point).toEqual({ x: 980, y: 150 });
  });
});
