import { describe, expect, it } from "vitest";
import {
  Spine38DragFacingDetector,
  orientPointOffset,
  orientRegionOffset,
  screenPointToSpineLocalPoint,
  screenRegionToSpineLocalRegion,
  slotRegionToScreenRegion,
  spineLocalPointToScreenPoint,
  spineLocalRegionToSlotRegion,
} from "./spine38Facing";

describe("Spine38DragFacingDetector", () => {
  it("switches left once after stable horizontal distance", () => {
    const detector = new Spine38DragFacingDetector("right");
    detector.begin({ x: 20, y: 10 });
    expect(detector.move({ x: 15, y: 10 })).toBeNull();
    expect(detector.move({ x: 12, y: 10 })).toBe("left");
    expect(detector.move({ x: 0, y: 10 })).toBeNull();
  });

  it("can reverse within the same drag", () => {
    const detector = new Spine38DragFacingDetector("right");
    detector.begin({ x: 20, y: 10 });
    expect(detector.move({ x: 10, y: 10 })).toBe("left");
    expect(detector.move({ x: 15, y: 10 })).toBeNull();
    expect(detector.move({ x: 18, y: 10 })).toBe("right");
  });

  it("ignores jitter and predominantly vertical movement", () => {
    const detector = new Spine38DragFacingDetector("right");
    detector.begin({ x: 10, y: 10 });
    expect(detector.move({ x: 7, y: 10 })).toBeNull();
    expect(detector.move({ x: 9, y: 10 })).toBeNull();
    expect(detector.move({ x: 6, y: 20 })).toBeNull();
    expect(detector.move({ x: 4, y: 30 })).toBeNull();
  });

  it("does not carry candidate distance into a new drag", () => {
    const detector = new Spine38DragFacingDetector("right");
    detector.begin({ x: 10, y: 10 });
    expect(detector.move({ x: 4, y: 10 })).toBeNull();
    detector.end();
    detector.begin({ x: 10, y: 10 });
    expect(detector.move({ x: 4, y: 10 })).toBeNull();
  });
});

describe("spine38 facing geometry", () => {
  const slotPosition = { x: 200, y: 100 };
  const slotScale = { x: 2, y: 2 };
  const spinePosition = { x: -10, y: 5 };
  const localRegion = { x: 2, y: 3, w: 20, h: 30 };

  it.each(["left", "right"] as const)(
    "keeps screen regions positive and round-trips for %s",
    (facing) => {
      const slotRegion = spineLocalRegionToSlotRegion(
        spinePosition,
        localRegion,
        facing,
      );
      const screenRegion = slotRegionToScreenRegion(
        slotPosition,
        slotScale,
        slotRegion,
      );
      expect(screenRegion.w).toBeGreaterThan(0);
      expect(screenRegion.h).toBeGreaterThan(0);
      expect(
        screenRegionToSpineLocalRegion(
          slotPosition,
          slotScale,
          spinePosition,
          screenRegion,
          facing,
        ),
      ).toEqual(localRegion);
    },
  );

  it.each(["left", "right"] as const)("round-trips points for %s", (facing) => {
    const point = { x: 7, y: 11 };
    const screenPoint = spineLocalPointToScreenPoint(
      slotPosition,
      slotScale,
      spinePosition,
      point,
      facing,
    );
    expect(
      screenPointToSpineLocalPoint(
        slotPosition,
        slotScale,
        spinePosition,
        screenPoint,
        facing,
      ),
    ).toEqual(point);
  });

  it("mirrors canonical region and point offsets to the other side", () => {
    expect(orientRegionOffset({ x: 10, y: 5, w: 20, h: 30 }, 100, "left")).toEqual({
      x: 70,
      y: 5,
      w: 20,
      h: 30,
    });
    expect(orientPointOffset({ x: 15, y: 8 }, 100, "left")).toEqual({
      x: 85,
      y: 8,
    });
  });
});
