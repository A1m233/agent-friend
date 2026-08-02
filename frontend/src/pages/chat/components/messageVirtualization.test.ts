import { describe, expect, it } from "vitest";
import { getMessageVirtualItemKey } from "./messageVirtualization";

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
