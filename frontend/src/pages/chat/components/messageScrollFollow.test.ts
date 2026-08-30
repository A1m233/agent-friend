import { describe, expect, it } from "vitest";
import {
  getMessageScrollDirection,
  isMessageScrollAtBottom,
  MESSAGE_SCROLL_BOTTOM_EPSILON_PX,
  reduceMessageScrollFollowing,
  reduceMessageScrollButtonVisible,
  shouldFollowAfterTotalSizeChange,
} from "./messageScrollFollow";

describe("message scroll following", () => {
  it("derives direction from actual viewport movement", () => {
    expect(getMessageScrollDirection(100, 99.2)).toBe("away");
    expect(getMessageScrollDirection(100, 100.8)).toBe("toward");
    expect(getMessageScrollDirection(100, 100)).toBe("none");
  });

  it("treats only a one-pixel rounding epsilon as the bottom", () => {
    expect(isMessageScrollAtBottom(1_000, 500, 500)).toBe(true);
    expect(isMessageScrollAtBottom(1_000, 499.5, 500)).toBe(true);
    expect(isMessageScrollAtBottom(1_000, 500 - MESSAGE_SCROLL_BOTTOM_EPSILON_PX, 500)).toBe(true);
    expect(isMessageScrollAtBottom(1_000, 498.9, 500)).toBe(false);
  });

  it("does not detach when virtual measurement creates an automatic viewport gap", () => {
    expect(reduceMessageScrollFollowing(true, {
      type: "viewport-scrolled",
      atBottom: false,
      source: "automatic",
    })).toBe(true);
  });

  it("detaches only when the user moves away and follows again at the bottom", () => {
    const detached = reduceMessageScrollFollowing(true, {
      type: "viewport-scrolled",
      atBottom: false,
      source: "user",
    });
    expect(detached).toBe(false);

    expect(reduceMessageScrollFollowing(detached, {
      type: "viewport-scrolled",
      atBottom: true,
      source: "user",
    })).toBe(true);
  });

  it("explicitly requests following after the user detached", () => {
    expect(reduceMessageScrollFollowing(false, { type: "follow-requested" })).toBe(true);
  });

  it("detaches immediately without flashing the button before the viewport leaves the bottom", () => {
    const detachEvent = { type: "detach-requested" } as const;
    expect(reduceMessageScrollFollowing(true, detachEvent)).toBe(false);
    expect(reduceMessageScrollButtonVisible(false, detachEvent)).toBe(false);

    const stillNearBottom = {
      type: "viewport-scrolled",
      atBottom: true,
      source: "user",
      direction: "away",
    } as const;
    expect(reduceMessageScrollFollowing(false, stillNearBottom)).toBe(false);
    expect(reduceMessageScrollButtonVisible(false, stillNearBottom)).toBe(false);

    const movedAway = { ...stillNearBottom, atBottom: false };
    expect(reduceMessageScrollButtonVisible(false, movedAway)).toBe(true);
  });

  it("keeps one smooth wheel gesture attached until it crosses the epsilon", () => {
    const detached = reduceMessageScrollFollowing(true, { type: "detach-requested" });
    const firstFrame = {
      type: "viewport-scrolled",
      atBottom: isMessageScrollAtBottom(1_000, 499.2, 500),
      source: "user",
      direction: "away",
    } as const;
    const afterFirstFrame = reduceMessageScrollFollowing(detached, firstFrame);
    expect(afterFirstFrame).toBe(false);
    expect(reduceMessageScrollButtonVisible(false, firstFrame)).toBe(false);

    const secondFrame = {
      ...firstFrame,
      atBottom: isMessageScrollAtBottom(1_000, 498.4, 500),
    };
    expect(reduceMessageScrollFollowing(afterFirstFrame, secondFrame)).toBe(false);
    expect(reduceMessageScrollButtonVisible(false, secondFrame)).toBe(true);
  });

  it("keeps button visibility stable across automatic virtual measurements", () => {
    const automaticGap = {
      type: "viewport-scrolled",
      atBottom: false,
      source: "automatic",
    } as const;
    expect(reduceMessageScrollButtonVisible(false, automaticGap)).toBe(false);
    expect(reduceMessageScrollButtonVisible(true, automaticGap)).toBe(true);
    expect(reduceMessageScrollButtonVisible(true, { type: "follow-requested" })).toBe(false);
  });

  it("reanchors after measured total size changes only while following", () => {
    expect(shouldFollowAfterTotalSizeChange(true, 1_000, 2_200)).toBe(true);
    expect(shouldFollowAfterTotalSizeChange(false, 1_000, 2_200)).toBe(false);
    expect(shouldFollowAfterTotalSizeChange(true, 2_200, 2_200)).toBe(false);
    expect(shouldFollowAfterTotalSizeChange(true, null, 2_200)).toBe(false);
    expect(shouldFollowAfterTotalSizeChange(true, 1_000, 2_200, true)).toBe(false);
  });
});
