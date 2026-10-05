import { describe, expect, it } from "vitest";
import type { DropEvent } from "react-dropzone";
import {
  extractClipboardFiles,
  filesFromDropEvent,
} from "./attachmentInput";

function clipboardItem(file: File | null) {
  return {
    kind: "file",
    getAsFile: () => file,
  } as const;
}

describe("extractClipboardFiles", () => {
  it("preserves clipboard item order and ignores empty file items", () => {
    const first = new File(["first"], "first.png", { type: "image/png" });
    const second = new File(["second"], "second.png", { type: "image/png" });

    expect(extractClipboardFiles({
      items: [clipboardItem(first), clipboardItem(null), clipboardItem(second)],
      files: [],
    })).toEqual([first, second]);
  });

  it("falls back to clipboard files when items do not expose a file", () => {
    const file = new File(["pdf"], "document.pdf", { type: "application/pdf" });

    expect(extractClipboardFiles({
      items: [{ kind: "string", getAsFile: () => null }],
      files: [file],
    })).toEqual([file]);
  });

  it("returns no attachment for ordinary text clipboard data", () => {
    expect(extractClipboardFiles({
      items: [{ kind: "string", getAsFile: () => null }],
      files: [],
    })).toEqual([]);
  });
});

describe("filesFromDropEvent", () => {
  it("uses the original transfer order even when dropzone splits accepted and rejected files", () => {
    const first = new File(["first"], "first.png", { type: "image/png" });
    const second = Object.assign(
      new File(["second"], "second.exe", { type: "application/octet-stream" }),
      { path: "second.exe", relativePath: "second.exe" },
    );
    const event = {
      dataTransfer: { files: [first, second] },
    } as unknown as DropEvent;

    expect(filesFromDropEvent(event, [first], [{ file: second, errors: [] }]))
      .toEqual([first, second]);
  });
});
