import { describe, expect, it } from "vitest";

import { joinVoiceDraft, transcriptDeltaAfterConsumed } from "./draft";

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

describe("transcriptDeltaAfterConsumed", () => {
  it("keeps full transcript when nothing has been consumed", () => {
    expect(transcriptDeltaAfterConsumed("继续说话", "")).toBe("继续说话");
  });

  it("keeps only the part after the consumed cumulative transcript", () => {
    expect(transcriptDeltaAfterConsumed("今天天气很好继续", "今天天气很好")).toBe("继续");
  });

  it("suppresses repeated cumulative transcripts", () => {
    expect(transcriptDeltaAfterConsumed("今天天气很好", "今天天气很好")).toBe("");
  });

  it("does not restore consumed text when the provider revises earlier words", () => {
    expect(transcriptDeltaAfterConsumed("今天天气不错继续", "今天天气很好")).toBe("继续");
  });

  it("supports clearing the draft and continuing dictation", () => {
    const nextTranscript = transcriptDeltaAfterConsumed("旧内容新内容", "旧内容");

    expect(joinVoiceDraft("", nextTranscript)).toBe("新内容");
  });
});
