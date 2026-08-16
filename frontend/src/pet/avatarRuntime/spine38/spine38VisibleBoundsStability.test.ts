import { describe, expect, it } from "vitest";

import {
  SPINE_VISIBLE_BOUNDS_SETTLE_MS,
  canSampleSpineVisibleBounds,
  createSpineVisibleBoundsStabilityState,
  observeSpineVisibleBoundsSample,
  resetPendingSpineVisibleBoundsForDisplayChange,
} from "./spine38VisibleBoundsStability";

const completeBounds = { x: -50, y: -100, w: 100, h: 128 };

describe("spine38VisibleBoundsStability", () => {
  it("waits for a quiet display interval and consecutive stable samples", () => {
    let state = createSpineVisibleBoundsStabilityState(1_000);

    expect(
      canSampleSpineVisibleBounds(
        state,
        1_000 + SPINE_VISIBLE_BOUNDS_SETTLE_MS - 1,
      ),
    ).toBe(false);
    state = observeSpineVisibleBoundsSample(
      state,
      completeBounds,
      1_000 + SPINE_VISIBLE_BOUNDS_SETTLE_MS - 1,
    );
    expect(state.candidateCount).toBe(0);

    const firstSampleAt = 1_000 + SPINE_VISIBLE_BOUNDS_SETTLE_MS;
    state = observeSpineVisibleBoundsSample(state, completeBounds, firstSampleAt);
    state = observeSpineVisibleBoundsSample(
      state,
      { ...completeBounds, h: 124 },
      firstSampleAt + 250,
    );
    expect(state.accepted).toBeNull();

    state = observeSpineVisibleBoundsSample(
      state,
      { ...completeBounds, h: 126 },
      firstSampleAt + 500,
    );
    expect(state.accepted).toEqual({ ...completeBounds, h: 126 });
  });

  it("rejects a transient partial framebuffer sample", () => {
    let state = createSpineVisibleBoundsStabilityState(0);
    const firstSampleAt = SPINE_VISIBLE_BOUNDS_SETTLE_MS;

    state = observeSpineVisibleBoundsSample(state, completeBounds, firstSampleAt);
    state = observeSpineVisibleBoundsSample(
      state,
      { ...completeBounds, h: 24 },
      firstSampleAt + 250,
    );

    expect(state.candidateCount).toBe(1);
    expect(state.candidate?.h).toBe(24);
    expect(state.accepted).toBeNull();
  });

  it("restarts settling before acceptance but preserves trusted model-local bounds", () => {
    let state = createSpineVisibleBoundsStabilityState(0);
    state = observeSpineVisibleBoundsSample(
      state,
      completeBounds,
      SPINE_VISIBLE_BOUNDS_SETTLE_MS,
      { requiredSamples: 1 },
    );
    const accepted = state;

    state = resetPendingSpineVisibleBoundsForDisplayChange(state, 5_000);
    expect(state).toBe(accepted);
    expect(state.accepted).toEqual(completeBounds);

    const pending = createSpineVisibleBoundsStabilityState(0);
    const reset = resetPendingSpineVisibleBoundsForDisplayChange(pending, 5_000);
    expect(reset.scanAfterMs).toBe(5_000 + SPINE_VISIBLE_BOUNDS_SETTLE_MS);
    expect(reset.candidate).toBeNull();
  });
});
