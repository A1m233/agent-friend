import { describe, expect, it } from "vitest";
import {
  ARK_AMIYA_ACTION_MAPPING,
  ARK_AMIYA_SPINE_FIXTURE_MODEL,
} from "@/avatar-models";
import {
  chooseInitialAnimation,
  isAvatarPhaseAction,
  resolveAnimationName,
  resolveSpineActionMapping,
} from "./actionMapping";

describe("avatar runtime action mapping", () => {
  it("resolves exact, case-insensitive, default and first-animation fallbacks", () => {
    expect(chooseInitialAnimation(["Default", "Relax"], "Default")).toEqual({
      animation: "Default",
    });
    expect(resolveAnimationName(["Default", "Relax"], "relax", "Default")).toEqual({
      animation: "Relax",
      fallbackReason: "case-insensitive match for relax",
    });
    expect(resolveAnimationName(["Default", "Relax"], "Sleep", "Default")).toEqual({
      animation: "Default",
      fallbackReason: "Sleep missing; using Default",
    });
    expect(resolveAnimationName(["Move"], "Sleep", "Default")).toEqual({
      animation: "Move",
      fallbackReason: "Default missing; using first animation Move",
    });
    expect(resolveAnimationName([], "Sleep", "Default")).toBeNull();
  });

  it("keeps phase actions separate from one-shot actions", () => {
    expect(isAvatarPhaseAction("speaking")).toBe(true);
    expect(isAvatarPhaseAction("sleep")).toBe(true);
    expect(isAvatarPhaseAction("tap")).toBe(false);
  });

  it("uses configured Spine mappings before generated fallbacks", () => {
    expect(resolveSpineActionMapping(ARK_AMIYA_SPINE_FIXTURE_MODEL, "idle")).toEqual(
      ARK_AMIYA_ACTION_MAPPING.idle,
    );
    expect(resolveSpineActionMapping(ARK_AMIYA_SPINE_FIXTURE_MODEL, "tap")).toEqual(
      ARK_AMIYA_ACTION_MAPPING.tap,
    );
    expect(
      resolveSpineActionMapping(
        {
          ...ARK_AMIYA_SPINE_FIXTURE_MODEL,
          spine: {
            ...ARK_AMIYA_SPINE_FIXTURE_MODEL.spine,
            actionMapping: {},
            defaultAnimation: "Default",
          },
        },
        "thinking",
      ),
    ).toEqual({ animation: "Default", loop: true, returnToPhase: false });
  });
});
