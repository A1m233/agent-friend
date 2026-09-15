// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatTextReference } from "@/types/chat";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const reference: ChatTextReference = {
  referenceId: "ref-1",
  quotedText: "quoted text",
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
    start: 0,
    end: 11,
  },
};

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("ComposerAttachmentStrip", () => {
  it("navigates from a quoted card and closes the popover without removing the draft", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const onSelect = vi.fn();
    const onRemove = vi.fn();
    const onClear = vi.fn();
    await act(async () => root.render(createElement(ComposerAttachmentStrip, {
      references: [reference], removable: true, onReferenceSelect: onSelect,
      onReferenceRemove: onRemove, onClearReferences: onClear,
    })));
    const trigger = container.querySelector<HTMLButtonElement>(".composer-reference-card-trigger")!;
    await act(async () => trigger.click());
    const card = [...document.querySelectorAll<HTMLButtonElement>('[role="listitem"] button')]
      .find((button) => button.textContent?.includes("quoted text"));
    expect(card).toBeDefined();
    await act(async () => card!.click());
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(reference);
    expect(onRemove).not.toHaveBeenCalled();
    expect(onClear).not.toHaveBeenCalled();
    expect(trigger.getAttribute("data-state")).toBe("closed");
    expect(document.querySelector('[role="listitem"]')).toBeNull();
    expect(trigger.textContent).toContain("1 个已选文本");
    await act(async () => root.unmount());
  });

  it("keeps the reference card and real files in the same ChatSender attachment slot", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const clearReferences = vi.fn();

    await act(async () => {
      root.render(createElement(ComposerAttachmentStrip, {
        attachmentContent: createElement("t-attachments", {
          className: "composer-draft-attachments",
          "data-item-count": 1,
        }),
        references: [reference],
        removable: true,
        onReferenceRemove: vi.fn(),
        onClearReferences: clearReferences,
      }));
    });

    const strip = container.querySelector(".composer-attachment-strip");
    expect(strip?.getAttribute("slot")).toBe("inner-header");
    expect(strip?.children[0]?.classList.contains("composer-reference-card")).toBe(true);
    expect(strip?.children[1]).toMatchObject({ tagName: "T-ATTACHMENTS" });
    expect(container.querySelector(".composer-reference-card-trigger")?.textContent)
      .toContain("1 个已选文本");
    expect(container.querySelector(".composer-reference-card-icon > svg")).not.toBeNull();
    expect(container.querySelector("t-attachments")?.getAttribute("data-item-count")).toBe("1");

    const clearButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="清空全部引用"]',
    );
    expect(clearButton?.classList.contains("composer-reference-card-remove")).toBe(true);
    expect(clearButton?.querySelector(".composer-reference-card-remove-icon")).not.toBeNull();

    const referenceButton = container.querySelector<HTMLButtonElement>(
      'button[aria-label="查看 1 个已选文本"]',
    );
    expect(referenceButton?.getAttribute("data-state")).toBe("closed");
    await act(async () => {
      clearButton?.dispatchEvent(new MouseEvent("pointerover", { bubbles: true }));
    });
    expect(referenceButton?.getAttribute("data-state")).toBe("closed");

    await act(async () => clearButton?.click());
    expect(clearReferences).toHaveBeenCalledOnce();

    await act(async () => root.unmount());
  });
});
