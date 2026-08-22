import { describe, expect, it } from "vitest";
import {
  presentFilesystemReadTextResult,
  presentToolPayload,
} from "./toolPayloadPresentation";

describe("tool payload presentation", () => {
  it("pretty-prints complete JSON payloads", () => {
    expect(presentToolPayload('{"ok":true,"items":[1,2]}')).toEqual({
      kind: "json",
      text: '{\n  "ok": true,\n  "items": [\n    1,\n    2\n  ]\n}',
      value: { ok: true, items: [1, 2] },
    });
  });

  it("preserves non-JSON and incomplete JSON verbatim", () => {
    expect(presentToolPayload("plain text")).toEqual({ kind: "text", text: "plain text" });
    expect(presentToolPayload('{"partial":')).toEqual({
      kind: "text",
      text: '{"partial":',
    });
  });

  it("extracts filesystem text without exposing JSON escapes", () => {
    const result = presentFilesystemReadTextResult(JSON.stringify({
      ok: true,
      path: "notes.md",
      content: "first\nsecond\n",
      encoding: "utf-8",
      start_line: 1,
      start_column: 0,
      end_line: 2,
      end_column: 7,
      total_lines: 4,
      truncated: true,
      next_start_line: 3,
      next_start_column: 0,
    }));

    expect(result).toEqual({
      content: "first\nsecond\n",
      metadata: "notes.md · UTF-8 · 第 1–2 行 / 共 4 行 · 已截断；下一段从第 3 行开始",
    });
  });
});
