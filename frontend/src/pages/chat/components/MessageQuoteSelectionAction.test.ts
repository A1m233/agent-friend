// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDraftStore } from "@/stores";
import { MessageQuoteSelectionAction } from "./MessageQuoteSelectionAction";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("MessageQuoteSelectionAction", () => {
  let container: HTMLDivElement;
  let root: Root;
  let originalRangeRect: PropertyDescriptor | undefined;

  beforeEach(async () => {
    useDraftStore.setState({ workspaces: {}, submissions: {} });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    originalRangeRect = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ left: 12, top: 16, right: 40, bottom: 32, width: 28, height: 16 }),
    });
    await act(async () => {
      root.render(createElement(MessageQuoteSelectionAction, { workspaceKey: "session-1" }));
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    vi.restoreAllMocks();
    document.body.replaceChildren();
    if (originalRangeRect) {
      Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRangeRect);
    } else {
      Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
    }
  });

  it("shows the quote action after selecting message text inside TDesign shadow roots", async () => {
    const quoteHost = document.createElement("div");
    quoteHost.dataset.quoteableText = "true";
    quoteHost.dataset.quoteMessageId = "assistant-1";
    quoteHost.dataset.quoteBlockId = "block-1";
    quoteHost.dataset.quoteRole = "assistant";
    const chatItem = document.createElement("t-chat-item");
    const chatItemRoot = chatItem.attachShadow({ mode: "open" });
    const markdown = document.createElement("t-chat-md-content");
    const markdownRoot = markdown.attachShadow({ mode: "open" });
    const selectable = document.createElement("span");
    const text = document.createTextNode("alpha beta gamma");
    selectable.append(text);
    markdownRoot.append(selectable);
    chatItemRoot.append(markdown);
    quoteHost.append(chatItem);
    document.body.append(quoteHost);

    const scopedRange = document.createRange();
    scopedRange.selectNode(chatItem);
    const selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => scopedRange,
      getComposedRanges: () => [{
        startContainer: text,
        startOffset: 6,
        endContainer: text,
        endOffset: 10,
      }],
      toString: () => "beta",
    } as unknown as Selection;
    vi.spyOn(window, "getSelection").mockReturnValue(selection);

    await act(async () => {
      selectable.dispatchEvent(new Event("pointerup", { bubbles: true, composed: true }));
    });

    const action = container.querySelector("button");
    expect(action).toBeInstanceOf(HTMLButtonElement);
    expect(action?.textContent).toContain("引用");
  });
});
