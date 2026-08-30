import { describe, expect, it } from "vitest";
import {
  getMessageVirtualItemKey,
  getScrollToBottomBehavior,
  shouldAdjustMessageScrollPositionOnItemSizeChange,
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

describe("dynamic message measurement", () => {
  it("does not compensate the message row whose disclosure the user toggled", () => {
    expect(shouldAdjustMessageScrollPositionOnItemSizeChange({
      activeMessageId: "message-2",
      itemKey: "message-2",
      itemStart: 200,
      scrollOffset: 600,
      scrollDirection: "forward",
      previouslyMeasured: true,
    })).toBe(false);
  });

  it("preserves the virtualizer default for unrelated row measurements", () => {
    expect(shouldAdjustMessageScrollPositionOnItemSizeChange({
      activeMessageId: "message-2",
      itemKey: "message-1",
      itemStart: 200,
      scrollOffset: 600,
      scrollDirection: "forward",
      previouslyMeasured: true,
    })).toBe(true);
    expect(shouldAdjustMessageScrollPositionOnItemSizeChange({
      activeMessageId: "message-2",
      itemKey: "message-1",
      itemStart: 200,
      scrollOffset: 600,
      scrollDirection: "backward",
      previouslyMeasured: true,
    })).toBe(false);
    expect(shouldAdjustMessageScrollPositionOnItemSizeChange({
      activeMessageId: "message-2",
      itemKey: "message-1",
      itemStart: 200,
      scrollOffset: 600,
      scrollDirection: "backward",
      previouslyMeasured: false,
    })).toBe(true);
    expect(shouldAdjustMessageScrollPositionOnItemSizeChange({
      activeMessageId: null,
      itemKey: "message-3",
      itemStart: 700,
      scrollOffset: 600,
      scrollDirection: null,
      previouslyMeasured: true,
    })).toBe(false);
  });
});
