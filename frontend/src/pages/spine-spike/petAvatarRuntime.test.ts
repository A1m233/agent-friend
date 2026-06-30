import { describe, expect, it } from "vitest";

import {
  ARK_ACTION_MAPPING,
  chooseInitialAnimation,
  isPhaseAction,
  resolveAnimationName,
} from "./petAvatarRuntime";

describe("petAvatarRuntime", () => {
  it("maps project-level pet actions onto known Arknights chibi animations", () => {
    expect(ARK_ACTION_MAPPING.idle).toEqual({ animation: "Relax", loop: true });
    expect(ARK_ACTION_MAPPING.tap).toEqual({
      animation: "Interact",
      loop: false,
      returnToPhase: true,
    });
    expect(ARK_ACTION_MAPPING.dragEnd.returnToPhase).toBe(true);
  });

  it("falls back from preferred animations without losing a playable loop", () => {
    const animations = ["Default", "Interact", "Relax"];

    expect(chooseInitialAnimation(animations, "Missing")).toBe("Default");
    expect(resolveAnimationName(animations, "relax", "Default")).toBe("Relax");
    expect(resolveAnimationName(animations, "Sleep", "Default")).toBe("Default");
  });

  it("keeps one-shot actions separate from runtime phases", () => {
    expect(isPhaseAction("speaking")).toBe(true);
    expect(isPhaseAction("tap")).toBe(false);
    expect(isPhaseAction("dragStart")).toBe(false);
  });
});
