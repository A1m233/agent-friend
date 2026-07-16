import { describe, expect, it } from "vitest";
import { isEventStreamContentType } from "./stream";

describe("isEventStreamContentType", () => {
  it("accepts SSE content types with parameters", () => {
    expect(isEventStreamContentType("text/event-stream; charset=utf-8")).toBe(true);
  });

  it("rejects packaged asset HTML", () => {
    expect(isEventStreamContentType("text/html")).toBe(false);
    expect(isEventStreamContentType(null)).toBe(false);
  });
});
