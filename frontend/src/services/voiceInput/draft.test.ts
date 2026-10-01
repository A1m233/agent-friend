import { describe, expect, it } from "vitest";

import { joinVoiceDraft } from "./draft";

describe("joinVoiceDraft", () => {
  it("appends Chinese transcript without forcing spaces", () => {
    expect(joinVoiceDraft("替我审批", "一下")).toBe("替我审批一下");
  });

  it("keeps existing whitespace", () => {
    expect(joinVoiceDraft("hello ", "world")).toBe("hello world");
    expect(joinVoiceDraft("hello", " world")).toBe("hello world");
  });

  it("adds a space between adjacent ascii words", () => {
    expect(joinVoiceDraft("hello", "world")).toBe("hello world");
  });
});
