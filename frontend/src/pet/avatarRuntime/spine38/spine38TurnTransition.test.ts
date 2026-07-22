import { describe, expect, it } from "vitest";
import {
  SPINE38_TURN_DURATION_MS,
  Spine38TurnTransition,
} from "./spine38TurnTransition";

describe("Spine38TurnTransition", () => {
  it("snaps immediately and ignores the same target", () => {
    const transition = new Spine38TurnTransition("right");
    expect(transition.snap("left")).toEqual({ scaleX: -1, settled: true });
    expect(transition.turnTo("left")).toBe(false);
    expect(transition.isAnimating).toBe(false);
  });

  it("smoothly reaches the target and reports completion", () => {
    const transition = new Spine38TurnTransition("right");
    expect(transition.turnTo("left")).toBe(true);
    const first = transition.update(SPINE38_TURN_DURATION_MS / 4);
    const second = transition.update(SPINE38_TURN_DURATION_MS / 4);
    expect(first.scaleX).toBeLessThan(1);
    expect(second.scaleX).toBeLessThan(first.scaleX);
    expect(second.settled).toBe(false);
    expect(transition.update(SPINE38_TURN_DURATION_MS / 2)).toEqual({
      scaleX: -1,
      settled: true,
      completedFacing: "left",
    });
  });

  it("retargets from the current scale without completing the old turn", () => {
    const transition = new Spine38TurnTransition("right");
    transition.turnTo("left");
    const midway = transition.update(SPINE38_TURN_DURATION_MS / 2).scaleX;
    expect(midway).toBe(0);
    transition.turnTo("right");
    const reversed = transition.update(SPINE38_TURN_DURATION_MS / 4);
    expect(reversed.scaleX).toBeGreaterThan(midway);
    expect(reversed.completedFacing).toBeUndefined();
    expect(transition.update(SPINE38_TURN_DURATION_MS)).toEqual({
      scaleX: 1,
      settled: true,
      completedFacing: "right",
    });
  });

  it("clamps a large ticker delta to the final state", () => {
    const transition = new Spine38TurnTransition("right");
    transition.turnTo("left");
    expect(transition.update(10_000)).toEqual({
      scaleX: -1,
      settled: true,
      completedFacing: "left",
    });
  });
});
