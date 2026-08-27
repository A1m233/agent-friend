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

describe("message quote selection", () => {
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
    Object.defineProperty(range, "getBoundingClientRect", {
      value: () => ({ left: 1, top: 2, right: 3, bottom: 4, width: 2, height: 2 }),
    });
    const selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => range,
    } as unknown as Selection;

    expect(captureQuoteSelection(selection)).toMatchObject({
      quotedText: "😀",
      start: 1,
      end: 3,
      messageEventUuid: "assistant-1",
      textBlockId: "block-1",
    });
  });

  it("uses composed ranges for text selected inside nested shadow roots", () => {
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
    const composedRect = { left: 4, top: 5, right: 8, bottom: 9, width: 4, height: 4 };
    const originalRect = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => composedRect,
    });
    const selection = {
      isCollapsed: false,
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
        rect: composedRect,
      });
    } finally {
      if (originalRect) {
        Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRect);
      } else {
        Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
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
    markdownRoot.append(document.createTextNode("alpha beta gamma"));
    host.append(markdown);
    document.body.append(host);

    const scopedRange = document.createRange();
    scopedRange.selectNode(markdown);
    const rect = { left: 1, top: 2, right: 3, bottom: 4, width: 2, height: 2 };
    Object.defineProperty(scopedRange, "getBoundingClientRect", { value: () => rect });
    const selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => scopedRange,
      toString: () => "beta",
    } as unknown as Selection;

    expect(captureQuoteSelection(selection)).toMatchObject({
      quotedText: "beta",
      start: 6,
      end: 10,
      rect,
    });
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
