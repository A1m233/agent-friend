import { describe, expect, it } from "vitest";

import { readCustomEventDetail, readStringEventValue } from "./webComponentEvents";

describe("webComponentEvents", () => {
  it("reads raw CustomEvent detail", () => {
    expect(readCustomEventDetail<string>({ detail: "hello" })).toBe("hello");
    expect(readCustomEventDetail<{ value: string }>({ detail: { value: "hello" } })).toEqual({
      value: "hello",
    });
  });

  it("reads string values from common web component event shapes", () => {
    expect(readStringEventValue({ detail: "hello" })).toBe("hello");
    expect(readStringEventValue({ detail: { value: "hello" } })).toBe("hello");
    expect(readStringEventValue({ target: { value: "hello" } })).toBe("hello");
  });

  it("returns null when no string value is present", () => {
    expect(readStringEventValue({ detail: 1 })).toBeNull();
    expect(readStringEventValue({ detail: { value: 1 } })).toBeNull();
    expect(readStringEventValue({ target: {} })).toBeNull();
    expect(readStringEventValue(null)).toBeNull();
  });
});
