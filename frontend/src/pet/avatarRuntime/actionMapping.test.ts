import { describe, expect, it } from "vitest";
import {
  ARK_AMIYA_SPINE_FIXTURE_MODEL,
  ARK_SPINE_ACTION_MAPPING,
  type AvatarModelAction,
  type SpineAvatarModelEntry,
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
      ARK_SPINE_ACTION_MAPPING.idle,
    );
    expect(resolveSpineActionMapping(ARK_AMIYA_SPINE_FIXTURE_MODEL, "tap")).toEqual(
      ARK_SPINE_ACTION_MAPPING.tap,
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

  it("infers conventional actions only when an unconfigured model provides them", () => {
    const entry = {
      ...ARK_AMIYA_SPINE_FIXTURE_MODEL,
      id: "user:spine38:test",
      source: "user",
      spine: {
        ...ARK_AMIYA_SPINE_FIXTURE_MODEL.spine,
        actionMapping: undefined,
        defaultAnimation: "Relax",
      },
    } satisfies SpineAvatarModelEntry;
    const animations = ["Default", "Interact", "Move", "Relax", "Sit", "Sleep"];

    for (const action of Object.keys(ARK_SPINE_ACTION_MAPPING) as AvatarModelAction[]) {
      expect(resolveSpineActionMapping(entry, action, animations)).toEqual(
        ARK_SPINE_ACTION_MAPPING[action],
      );
    }

    expect(resolveSpineActionMapping(entry, "tap", ["Relax"])).toEqual({
      animation: "Relax",
      loop: false,
      returnToPhase: true,
    });
    expect(resolveSpineActionMapping(entry, "tap", ["interact", "Relax"])).toEqual({
      animation: "interact",
      loop: false,
      returnToPhase: true,
    });
  });
});
