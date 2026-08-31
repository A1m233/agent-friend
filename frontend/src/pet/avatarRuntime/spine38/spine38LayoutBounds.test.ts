import { describe, expect, it } from "vitest";
import type { SpineAvatarModelEntry } from "@/avatar-models";
import {
  resolveSpine38CanonicalLayoutAnimationNames,
  sampleSpine38AnimationBounds,
} from "./spine38LayoutBounds";

function makeTrack() {
  return {
    trackIndex: 0,
    loop: false,
    animationEnd: 0,
    listener: {},
    delay: 0,
    trackTime: 0,
    trackLast: 0,
    nextTrackLast: 0,
    trackEnd: 0,
    timeScale: 1,
    alpha: 1,
    mixTime: 0,
    mixDuration: 0,
    interruptAlpha: 1,
    totalAlpha: 1,
  };
}

function makeFakeSpine() {
  let activeAnimation = "";
  let activeTime = 0;
  let activeTrack = makeTrack();

  return {
    skeleton: {
      data: {
        animations: [
          { name: "Default", duration: 1 },
          { name: "Move", duration: 0 },
        ],
      },
      updateWorldTransform: () => {},
      setToSetupPose: () => {},
      getBounds: (
        offset: { set: (x: number, y: number) => void },
        size: { set: (x: number, y: number) => void },
      ) => {
        const bounds =
          activeAnimation !== "Default"
            ? { x: 0, y: 0, w: 40, h: 50 }
            : activeTime < 0.5
              ? { x: 0, y: 0, w: 100, h: 200 }
              : activeTime < 1
                ? { x: -10, y: -20, w: 130, h: 220 }
                : { x: 5, y: -5, w: 120, h: 210 };
        offset.set(bounds.x, bounds.y);
        size.set(bounds.w, bounds.h);
      },
    },
    state: {
      setAnimation: (_trackIndex: number, animationName: string) => {
        activeAnimation = animationName;
        activeTrack = makeTrack();
        return activeTrack;
      },
      apply: () => {
        activeTime = activeTrack.trackTime;
        return true;
      },
      clearTracks: () => {
        activeAnimation = "";
      },
    },
  };
}

describe("spine38LayoutBounds", () => {
  it("resolves the canonical layout animation through the idle action mapping", () => {
    const entry = {
      spine: {
        defaultAnimation: "Default",
        actionMapping: {
          idle: { animation: "default", loop: true },
        },
      },
    } as SpineAvatarModelEntry;

    expect(
      resolveSpine38CanonicalLayoutAnimationNames(entry, ["Default", "Move"]),
    ).toEqual(["Default"]);
  });

  it("includes an inferred conventional idle animation for unconfigured models", () => {
    const entry = {
      spine: {
        defaultAnimation: "Default",
      },
    } as SpineAvatarModelEntry;

    expect(
      resolveSpine38CanonicalLayoutAnimationNames(entry, ["Default", "Relax"]),
    ).toEqual(["Relax", "Default"]);
  });

  it("samples an animation and unions its bounds over time", () => {
    const bounds = sampleSpine38AnimationBounds(
      makeFakeSpine() as never,
      ["Default"],
      0.5,
    );

    expect(bounds).toEqual({ x: -10, y: -20, w: 135, h: 225 });
  });
});
