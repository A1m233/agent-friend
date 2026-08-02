import { describe, expect, it } from "vitest";
import {
  reduceMessageScrollFollowing,
  shouldFollowAfterTotalSizeChange,
} from "./messageScrollFollow";

describe("message scroll following", () => {
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

  it("reanchors after measured total size changes only while following", () => {
    expect(shouldFollowAfterTotalSizeChange(true, 1_000, 2_200)).toBe(true);
    expect(shouldFollowAfterTotalSizeChange(false, 1_000, 2_200)).toBe(false);
    expect(shouldFollowAfterTotalSizeChange(true, 2_200, 2_200)).toBe(false);
    expect(shouldFollowAfterTotalSizeChange(true, null, 2_200)).toBe(false);
  });
});
