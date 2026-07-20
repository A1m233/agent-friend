import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  nextPetInitialPlacementSettled,
  shouldSchedulePetAnchorPersistence,
} from "./petPlacementPersistence";

function scheduleSaveWhenAllowed(
  initialPlacementSettled: boolean,
  save: () => void,
): void {
  if (
    !shouldSchedulePetAnchorPersistence({
      isTauri: true,
      initialPlacementSettled,
      isDragging: false,
      hasPlacementIntent: false,
    })
  ) {
    return;
  }
  setTimeout(save, 500);
}

describe("pet placement persistence · initialization lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not save the renderer startup position after initialization fails", () => {
    const save = vi.fn();
    const settled = nextPetInitialPlacementSettled(false, { type: "failed" });

    scheduleSaveWhenAllowed(settled, save);
    vi.advanceTimersByTime(500);

    expect(settled).toBe(false);
    expect(save).not.toHaveBeenCalled();
  });

  it("allows a new anchor to be saved after the user takes ownership", () => {
    const save = vi.fn();
    const failed = nextPetInitialPlacementSettled(false, { type: "failed" });
    const userOwned = nextPetInitialPlacementSettled(failed, {
      type: "user-owned",
    });

    scheduleSaveWhenAllowed(userOwned, save);
    vi.advanceTimersByTime(499);
    expect(save).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(userOwned).toBe(true);
    expect(save).toHaveBeenCalledOnce();
  });

  it("settles only when initialization resolves to a usable point", () => {
    expect(
      nextPetInitialPlacementSettled(false, {
        type: "resolved",
        hasPoint: false,
        userMoved: false,
      }),
    ).toBe(false);
    expect(
      nextPetInitialPlacementSettled(false, {
        type: "resolved",
        hasPoint: true,
        userMoved: false,
      }),
    ).toBe(true);
  });

  it("keeps saving blocked while dragging or applying a placement intent", () => {
    const base = {
      isTauri: true,
      initialPlacementSettled: true,
    };

    expect(
      shouldSchedulePetAnchorPersistence({
        ...base,
        isDragging: true,
        hasPlacementIntent: false,
      }),
    ).toBe(false);
    expect(
      shouldSchedulePetAnchorPersistence({
        ...base,
        isDragging: false,
        hasPlacementIntent: true,
      }),
    ).toBe(false);
  });
});
