import { describe, expect, it } from "vitest";
import { VoiceDraftSession } from "./draftSession";
import type { VoiceInputTranscript } from "./types";

const event = (text: string, seq: number, extra: Partial<VoiceInputTranscript> = {}): VoiceInputTranscript => ({
  traceId: "trace", targetWorkspaceKey: "chat", kind: "partial", text, seq, ...extra,
});

describe("voice and keyboard draft ownership", () => {
  it("keeps manual corrections after the partial event was consumed", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    expect(session.apply(event("今天天气很好", 1)).text).toBe("今天天气很好");
    session.edit("今天天气不错", 6);
    expect(session.apply(event("今天天气很好我们出去走走", 2)).text).toBe("今天天气不错我们出去走走");
  });

  it("supports repeated edits, clearing and continued speech without replay", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("hello", 1));
    session.edit("hi", 2);
    expect(session.apply(event("hello world", 2)).text).toBe("hi world");
    session.edit("", 0);
    expect(session.apply(event("hello world again", 3)).text).toBe("again");
    expect(session.apply(event("hello world again", 4, { kind: "final" })).text).toBe("again");
    expect(session.apply(event("hello world again old", 5)).text).toBe("again");
  });

  it("updates only its inserted range and reanchors when the caret moves", () => {
    const session = new VoiceDraftSession("chat", "前后", 1);
    expect(session.apply(event("中", 1)).text).toBe("前中后");
    expect(session.apply(event("中间", 2)).text).toBe("前中间后");
    session.edit("前中间后", 0);
    expect(session.apply(event("中间新增", 3)).text).toBe("新增前中间后");
  });

  it("defers ASR updates during IME composition without consuming unseen speech", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("今天", 1));
    session.edit("今天", 2, true);
    session.edit("今天hao", 5, true);
    expect(session.apply(event("今天下雨", 2)).text).toBe("今天hao");
    session.edit("今天好", 3, false);
    expect(session.flush()?.text).toBe("今天好下雨");
  });

  it("uses audio time when a consumed number changes length", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("一百个苹果", 1, { spans: [{ text: "一百个苹果", startMs: 0, endMs: 1000 }] }));
    session.edit("两百个苹果", 5);
    const result = session.apply(event("100个苹果然后回家", 2, { spans: [
      { text: "100个苹果", startMs: 0, endMs: 1000 },
      { text: "然后回家", startMs: 1100, endMs: 2000 },
    ] }));
    expect(result.text).toBe("两百个苹果然后回家");
    expect(result.conflict).toBe(false);
  });

  it("preserves user text on an ambiguous revision instead of guessing a length", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("一百个苹果", 1));
    session.edit("两百个苹果", 5);
    expect(session.apply(event("100个苹果然后回家", 2))).toMatchObject({ text: "两百个苹果", conflict: true });
  });

  it("ignores stale, foreign and closed-session events", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("新", 2));
    expect(session.apply(event("旧", 1)).text).toBe("新");
    expect(session.apply(event("别的", 3, { traceId: "other" })).text).toBe("新");
    expect(session.apply(event("别的", 4, { targetWorkspaceKey: "other" })).text).toBe("新");
    session.close();
    expect(session.apply(event("迟到", 5)).text).toBe("新");
  });

  it("keeps deletions when only a correction to consumed speech arrives", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("100个", 1, { spans: [{ text: "100个", startMs: 0, endMs: 800 }] }));
    session.edit("", 0);
    expect(session.apply(event("百个", 2, { spans: [{ text: "百个", startMs: 0, endMs: 800 }] })).text).toBe("");
    expect(session.apply(event("百个苹果", 3, { spans: [
      { text: "百个", startMs: 0, endMs: 800 }, { text: "苹果", startMs: 900, endMs: 1200 },
    ] })).text).toBe("苹果");
  });

  it("preserves pasted content and emoji when the active speech gets shorter", () => {
    const session = new VoiceDraftSession("chat", "🙂后", 2);
    session.apply(event("很长的识别", 1));
    expect(session.apply(event("短句", 2)).text).toBe("🙂短句后");
    session.edit("🙂粘贴\n短句后", 6);
    expect(session.apply(event("短句继续", 3)).text).toBe("🙂粘贴\n短继续句后");
  });

  it("does not let a trailing partial replace a final buffered during composition", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.edit("", 0, true);
    session.apply(event("完成", 1, { kind: "final" }));
    session.apply(event("过时", 2));
    session.edit("手写", 2);
    expect(session.flush()?.text).toBe("手写完成");
  });

  it("rejects a timed segment straddling the owned audio boundary", () => {
    const session = new VoiceDraftSession("chat", "", 0);
    session.apply(event("旧字", 1, { spans: [{ text: "旧字", startMs: 0, endMs: 500 }] }));
    session.edit("改字", 2);
    expect(session.apply(event("修订新字", 2, {
      spans: [{ text: "修订新字", startMs: 0, endMs: 900 }],
    }))).toMatchObject({ text: "改字", conflict: true });
  });
});
