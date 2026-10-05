// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatTextReference } from "@/types/chat";
import {
  captureQuoteSelection,
  findReferenceMessageIndex,
  installReferenceHighlight,
  REFERENCE_HIGHLIGHT_NAME,
} from "./messageQuoteSelection";

function reference(
  quotedText: string,
  start: number,
  end: number,
): ChatTextReference {
  return {
    referenceId: "ref-1",
    quotedText,
    sourceRole: "assistant",
    availability: "available",
    source: {
      kind: "session_message",
      messageEventUuid: "assistant-1",
      textBlockId: "block-1",
      externalSourceKey: null,
    },
    locator: {
      kind: "text_range",
      coordinateSpace: "rendered_text_utf16_v1",
      start,
      end,
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

function shadowFixture() {
  const host = document.createElement("div");
  Object.assign(host.dataset, { quoteableText: "true", quoteMessageId: "message", quoteBlockId: "block", quoteRole: "assistant" });
  const item = document.createElement("t-chat-item");
  const shell = item.attachShadow({ mode: "open" });
  const avatar = document.createElement("div");
  avatar.append(document.createElement("slot"));
  const contentSlot = document.createElement("slot");
  contentSlot.name = "content";
  const trailing = document.createElement("div");
  shell.append(avatar, contentSlot, trailing);
  const markdown = document.createElement("t-chat-md-content");
  markdown.slot = "content";
  const body = markdown.attachShadow({ mode: "open" });
  body.innerHTML = "<h3>文件链接验收</h3><p><a>内联文件</a></p><table><thead><tr><th>类型</th></tr></thead></table>";
  item.append(markdown);
  host.append(item);
  document.body.append(host);
  const heading = body.querySelector("h3")!.firstChild!;
  const header = body.querySelector("th")!.firstChild!;
  const scoped = document.createRange();
  scoped.setStart(host, 0);
  scoped.collapse(true);
  const selection = (startContainer: Node, startOffset: number, endContainer: Node, endOffset: number) => ({
    rangeCount: 1, isCollapsed: true, toString: () => "\n", getRangeAt: () => scoped,
    getComposedRanges: () => [{ startContainer, startOffset, endContainer, endOffset }],
  } as unknown as Selection);
  return { host, avatar, trailing, heading, header, selection };
}

function captureWithGeometry(selection: Selection) {
  const key = "getClientRects";
  const original = Object.getOwnPropertyDescriptor(Range.prototype, key);
  Object.defineProperty(Range.prototype, key, { configurable: true,
    value(this: Range) {
      // A collapsed range is the original failure: no selected glyph geometry.
      return this.collapsed ? [] : [DOMRect.fromRect({ x: 10, y: 20, width: 100, height: 16 })];
    },
  });
  try { return captureQuoteSelection(selection); }
  finally {
    if (original) Object.defineProperty(Range.prototype, key, original);
    else Reflect.deleteProperty(Range.prototype, key);
  }
}

describe("message quote selection", () => {
  it("trusts exact ordinary DOM offsets when browser selected text adds paragraph breaks", () => {
    const host = document.createElement("div");
    Object.assign(host.dataset, { quoteableText: "true", quoteMessageId: "message", quoteBlockId: "block", quoteRole: "user" });
    host.innerHTML = "<p>第一段</p><p>第二段</p>";
    document.body.append(host);
    const range = document.createRange();
    range.selectNodeContents(host);
    expect(captureWithGeometry({ rangeCount: 1, getRangeAt: () => range, toString: () => "第一段\n第二段" } as unknown as Selection))
      .toMatchObject({ quotedText: "第一段第二段", start: 0, end: 6 });
  });

  it("captures a drag from an empty outer layout node into a nested Markdown table", () => {
    const f = shadowFixture();
    expect(captureWithGeometry(f.selection(f.avatar, 0, f.header, 2))).toMatchObject({
      quotedText: "文件链接验收内联文件类型", start: 0, end: 12,
      messageEventUuid: "message", textBlockId: "block", rect: { left: 10, top: 20, width: 100 },
    });
  });

  it("maps an end boundary in the outer trailing layout without losing nested text", () => {
    const f = shadowFixture();
    expect(captureWithGeometry(f.selection(f.heading, 2, f.trailing, 0))).toMatchObject({
      quotedText: "链接验收内联文件类型", start: 2, end: 12,
    });
  });

  it("rejects endpoints in another message or an excluded action even with a retargeted host range", () => {
    const f = shadowFixture();
    const other = shadowFixture();
    expect(captureWithGeometry(f.selection(f.heading, 0, other.header, 2))).toBeNull();
    f.trailing.dataset.quoteExclude = "true";
    f.trailing.textContent = "复制";
    expect(captureWithGeometry(f.selection(f.heading, 0, f.trailing.firstChild!, 2))).toBeNull();
    expect(captureWithGeometry(f.selection(f.header, 1, f.header, 1))).toBeNull();
  });

  it("keeps highlights valid when the rendered quote crosses shadow trees", () => {
    const f = shadowFixture();
    const outerText = document.createTextNode("尾部");
    f.trailing.append(outerText);
    const set = vi.fn();
    vi.stubGlobal("CSS", { highlights: { set, delete: vi.fn() } });
    vi.stubGlobal("Highlight", class { constructor(...ranges: Range[]) {
      expect(ranges.every(range => !range.collapsed)).toBe(true);
      expect(ranges.map(range => range.toString()).join("")).toBe("类型尾部");
    } });
    expect(installReferenceHighlight(f.host, reference("类型尾部", 10, 14))).toBeTypeOf("function");
    expect(set).toHaveBeenCalledOnce();
  });

  it("captures rendered UTF-16 offsets including surrogate pairs", () => {
    const host = document.createElement("div");
    host.dataset.quoteableText = "true";
    host.dataset.quoteMessageId = "assistant-1";
    host.dataset.quoteBlockId = "block-1";
    host.dataset.quoteRole = "assistant";
    host.textContent = "A😀BC";
    document.body.append(host);
    const text = host.firstChild as Text;
    const range = document.createRange();
    range.setStart(text, 1);
    range.setEnd(text, 3);
    const originalRects = Object.getOwnPropertyDescriptor(Range.prototype, "getClientRects");
    Object.defineProperty(Range.prototype, "getClientRects", {
      configurable: true,
      value: () => [{ left: 1, top: 2, right: 3, bottom: 4, width: 2, height: 2 }],
    });
    const selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => range,
      toString: () => "😀",
    } as unknown as Selection;

    try {
      expect(captureQuoteSelection(selection)).toMatchObject({
        quotedText: "😀",
        start: 1,
        end: 3,
        messageEventUuid: "assistant-1",
        textBlockId: "block-1",
      });
    } finally {
      if (originalRects) {
        Object.defineProperty(Range.prototype, "getClientRects", originalRects);
      } else {
        Reflect.deleteProperty(Range.prototype, "getClientRects");
      }
    }
  });

  it("uses composed ranges when WebView2 reports a nested shadow selection as collapsed", () => {
    const host = document.createElement("div");
    host.dataset.quoteableText = "true";
    host.dataset.quoteMessageId = "assistant-shadow";
    host.dataset.quoteBlockId = "block-shadow";
    host.dataset.quoteRole = "assistant";
    const chatItem = document.createElement("t-chat-item");
    const chatItemRoot = chatItem.attachShadow({ mode: "open" });
    const markdown = document.createElement("t-chat-md-content");
    const markdownRoot = markdown.attachShadow({ mode: "open" });
    const text = document.createTextNode("alpha beta gamma");
    markdownRoot.append(text);
    chatItemRoot.append(markdown);
    host.append(chatItem);
    document.body.append(host);

    const scopedRange = document.createRange();
    scopedRange.selectNode(chatItem);
    const firstLineStartRect = { left: 4, top: 5, right: 8, bottom: 9, width: 4, height: 4 };
    const firstLineEndRect = { left: 8, top: 6, right: 12, bottom: 10, width: 4, height: 4 };
    const secondLineRect = { left: 4, top: 12, right: 12, bottom: 20, width: 8, height: 8 };
    const fullSelectionRect = { left: 4, top: 5, right: 12, bottom: 20, width: 8, height: 15 };
    const originalRect = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
    const originalRects = Object.getOwnPropertyDescriptor(Range.prototype, "getClientRects");
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => fullSelectionRect,
    });
    Object.defineProperty(Range.prototype, "getClientRects", {
      configurable: true,
      value: () => [fullSelectionRect, firstLineStartRect, secondLineRect, firstLineEndRect],
    });
    const selection = {
      isCollapsed: true,
      rangeCount: 1,
      getRangeAt: () => scopedRange,
      getComposedRanges: ({ shadowRoots }: { shadowRoots: ShadowRoot[] }) => {
        expect(shadowRoots).toEqual([chatItemRoot, markdownRoot]);
        return [{
          startContainer: text,
          startOffset: 6,
          endContainer: text,
          endOffset: 10,
        }];
      },
      toString: () => "beta",
    } as unknown as Selection;

    try {
      expect(captureQuoteSelection(selection)).toMatchObject({
        quotedText: "beta",
        start: 6,
        end: 10,
        messageEventUuid: "assistant-shadow",
        textBlockId: "block-shadow",
        rect: { left: 4, top: 5, right: 12, bottom: 10, width: 8, height: 5 },
      });
    } finally {
      if (originalRect) {
        Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRect);
      } else {
        Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
      }
      if (originalRects) {
        Object.defineProperty(Range.prototype, "getClientRects", originalRects);
      } else {
        Reflect.deleteProperty(Range.prototype, "getClientRects");
      }
    }
  });

  it("falls back to a unique selected snapshot when composed ranges are unavailable", () => {
    const host = document.createElement("div");
    host.dataset.quoteableText = "true";
    host.dataset.quoteMessageId = "assistant-fallback";
    host.dataset.quoteBlockId = "block-fallback";
    host.dataset.quoteRole = "assistant";
    const markdown = document.createElement("t-chat-md-content");
    const markdownRoot = markdown.attachShadow({ mode: "open" });
    const paragraph = document.createElement("p");
    const prefix = document.createTextNode("alpha ");
    const strong = document.createElement("strong");
    const selectedText = document.createTextNode("beta");
    const suffix = document.createTextNode(" gamma");
    strong.append(selectedText);
    paragraph.append(prefix, strong, suffix);
    markdownRoot.append(paragraph);
    host.append(markdown);
    document.body.append(host);

    const scopedRange = document.createRange();
    scopedRange.selectNode(markdown);
    const hostRect = { left: 1, top: 2, right: 101, bottom: 22, width: 100, height: 20 };
    const selectedRect = { left: 42, top: 8, right: 74, bottom: 20, width: 32, height: 12 };
    const originalRect = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
    const originalRects = Object.getOwnPropertyDescriptor(Range.prototype, "getClientRects");
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => hostRect,
    });
    Object.defineProperty(Range.prototype, "getClientRects", {
      configurable: true,
      value(this: Range) {
        return this.startContainer === selectedText
          && this.startOffset === 0
          && this.endContainer === selectedText
          && this.endOffset === selectedText.length
          ? [selectedRect]
          : [hostRect];
      },
    });
    const selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => scopedRange,
      toString: () => "beta",
    } as unknown as Selection;

    try {
      expect(captureQuoteSelection(selection)).toMatchObject({
        quotedText: "beta",
        start: 6,
        end: 10,
        rect: selectedRect,
      });
    } finally {
      if (originalRect) {
        Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRect);
      } else {
        Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
      }
      if (originalRects) {
        Object.defineProperty(Range.prototype, "getClientRects", originalRects);
      } else {
        Reflect.deleteProperty(Range.prototype, "getClientRects");
      }
    }
  });

  it("only falls back to snapshot search when the text is unique", () => {
    const host = document.createElement("div");
    host.textContent = "alpha beta alpha";
    document.body.append(host);
    const set = vi.fn();
    const remove = vi.fn(() => true);
    vi.stubGlobal("CSS", { highlights: { set, delete: remove } });
    vi.stubGlobal("Highlight", class { constructor(_range: Range) {} });

    expect(installReferenceHighlight(host, reference("alpha", 99, 104))).toBeNull();
    const cleanup = installReferenceHighlight(host, reference("beta", 99, 103));

    expect(cleanup).toBeTypeOf("function");
    expect(set).toHaveBeenCalledWith(REFERENCE_HIGHLIGHT_NAME, expect.anything());
    cleanup?.();
    expect(remove).toHaveBeenCalledWith(REFERENCE_HIGHLIGHT_NAME);
  });

  it("finds a virtualized target by event id or text block id", () => {
    const messages = [
      {
        id: "assistant-1",
        role: "assistant" as const,
        createdAt: "2026-08-27T00:00:00Z",
        blocks: [{ kind: "text" as const, mid: "block-1", text: "answer" }],
        status: "complete" as const,
      },
    ];

    expect(findReferenceMessageIndex(messages, reference("answer", 0, 6))).toBe(0);
  });
});
