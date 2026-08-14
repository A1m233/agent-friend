import { describe, expect, it } from "vitest";
import {
  getMessageVirtualItemKey,
  getScrollToBottomBehavior,
} from "./messageVirtualization";

describe("getMessageVirtualItemKey", () => {
  it("keeps existing message keys stable when older history is prepended", () => {
    const current = [{ id: "message-2" }, { id: "message-3" }];
    const withHistory = [{ id: "message-1" }, ...current];

    expect(getMessageVirtualItemKey(current, 0)).toBe("message-2");
    expect(getMessageVirtualItemKey(withHistory, 1)).toBe("message-2");
  });

  it("falls back to the index for a transient out-of-range lookup", () => {
    expect(getMessageVirtualItemKey([], 4)).toBe(4);
  });
});

describe("getScrollToBottomBehavior", () => {
  it("keeps smooth scrolling within two viewports of the bottom", () => {
    expect(getScrollToBottomBehavior(2_000, 800, 400)).toBe("smooth");
  });

  it("jumps instantly when the bottom is farther than two viewports", () => {
    expect(getScrollToBottomBehavior(4_000, 800, 400)).toBe("auto");
  });

  it("treats bottom overscroll as a nearby target", () => {
    expect(getScrollToBottomBehavior(1_000, 650, 400)).toBe("smooth");
  });
});
