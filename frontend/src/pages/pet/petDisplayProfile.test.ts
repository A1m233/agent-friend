import { describe, expect, it } from "vitest";
import {
  PET_MINI_SCALE,
  computeEdgeDockPlacement,
  resolvePetDisplayProfile,
  type PetRect,
  type PetViewport,
} from "./petDisplayProfile";

describe("petDisplayProfile · avatar size and edge dock placement", () => {
  const viewport: PetViewport = { x: 0, y: 0, w: 1000, h: 800 };
  const rect: PetRect = { x: 420, y: 300, w: 200, h: 300 };

  it("normal avatar size keeps scale 1 and does not force placement", () => {
    const profile = resolvePetDisplayProfile({
      avatarSize: "normal",
      rect,
      viewport,
    });

    expect(profile).toEqual({
      avatarSize: "normal",
      placementKey: "init->normal",
      scale: 1,
      placementPoint: null,
      edge: null,
    });
  });

  it("mini avatar size is independent from edge docking", () => {
    const profile = resolvePetDisplayProfile({
      avatarSize: "mini",
      rect,
      viewport,
    });

    expect(profile).toEqual({
      avatarSize: "mini",
      placementKey: "init->mini",
      scale: PET_MINI_SCALE,
      placementPoint: null,
      edge: null,
    });
  });

  it("normal avatar size clamps center when restoring from mini", () => {
    const edgeRect: PetRect = { x: -30, y: 320, w: 100, h: 150 };

    const profile = resolvePetDisplayProfile({
      avatarSize: "normal",
      rect: edgeRect,
      viewport,
      previousAvatarSize: "mini",
    });

    expect(profile.avatarSize).toBe("normal");
    expect(profile.scale).toBe(1);
    expect(profile.placementPoint).toEqual({ x: 58, y: 395 });
  });

  it("switching from normal to mini scales avatar and moves it to an edge", () => {
    const profile = resolvePetDisplayProfile({
      avatarSize: "mini",
      rect,
      viewport,
      previousAvatarSize: "normal",
    });

    expect(profile.avatarSize).toBe("mini");
    expect(profile.scale).toBe(PET_MINI_SCALE);
    expect(profile.placementPoint).not.toBeNull();
    expect(profile.edge).not.toBeNull();
  });

  it("already-mini renders do not force edge placement", () => {
    const profile = resolvePetDisplayProfile({
      avatarSize: "mini",
      rect,
      viewport,
      previousAvatarSize: "mini",
    });

    expect(profile.scale).toBe(PET_MINI_SCALE);
    expect(profile.placementPoint).toBeNull();
    expect(profile.edge).toBeNull();
  });

  it("places left edge dock partly off screen with a visible recall area", () => {
    const { point, edge } = computeEdgeDockPlacement(rect, viewport, {
      edge: "left",
      scale: PET_MINI_SCALE,
    });

    expect(edge).toBe("left");
    expect(point.x).toBe(20);
    expect(point.y).toBe(450);
  });

  it("places right edge dock symmetrically", () => {
    const { point, edge } = computeEdgeDockPlacement(rect, viewport, {
      edge: "right",
      scale: PET_MINI_SCALE,
    });

    expect(edge).toBe("right");
    expect(point.x).toBe(980);
    expect(point.y).toBe(450);
  });

  it("clamps the free axis inside the viewport", () => {
    const lowRect: PetRect = { x: 420, y: 760, w: 200, h: 300 };

    const { point } = computeEdgeDockPlacement(lowRect, viewport, {
      edge: "left",
      scale: PET_MINI_SCALE,
    });

    expect(point.y).toBe(717);
  });

  it("chooses only between left and right edges", () => {
    const topRect: PetRect = { x: 420, y: 0, w: 200, h: 300 };

    const { edge, point } = computeEdgeDockPlacement(topRect, viewport, {
      scale: PET_MINI_SCALE,
    });

    expect(edge).toBe("right");
    expect(point).toEqual({ x: 980, y: 150 });
  });
});
