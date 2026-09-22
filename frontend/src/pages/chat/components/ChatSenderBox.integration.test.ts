// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { ChatSenderBox } from "./ChatSenderBox";

vi.hoisted(() => {
  // jsdom does not render constructable stylesheets; keep real component behavior.
  Object.defineProperty(document, "adoptedStyleSheets", { value: [], configurable: true });
  CSSStyleSheet.prototype.replaceSync = () => {};
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Use the installed React wrapper and patched web component, not a sender mock.
function findInShadowRoots(root: ParentNode, selector: string): HTMLElement | null {
  const match = root.querySelector<HTMLElement>(selector);
  if (match) return match;
  for (const child of root.querySelectorAll("*")) {
    if (child.shadowRoot) {
      const nested = findInShadowRoots(child.shadowRoot, selector);
      if (nested) return nested;
    }
  }
  return null;
}

describe("ChatSenderBox real component integration", () => {
  it.each(["callback", "boolean"] as const)("updates non-text send eligibility through a %s prop", async (mode) => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const onSend = vi.fn();
    const render = async (hasReadyContent: boolean) => {
      await act(async () => root.render(createElement(ChatSenderBox, {
        placement: "bottom",
        value: "",
        onSend,
        sendBtnDisabled: mode === "callback"
          ? (value) => !value.trim() && !hasReadyContent
          : !hasReadyContent,
      })));
    };
    try {
      await render(false);
      const button = () => findInShadowRoots(container, "button") as HTMLButtonElement;
      expect(button()).not.toBeNull();
      expect(button().disabled).toBe(true);

      await render(true);
      expect(button().disabled).toBe(false);
      await act(async () => button().click());
      expect(onSend).toHaveBeenCalledOnce();
      expect(onSend.mock.calls[0][0].detail.value).toBe("");

      const pressEnter = async () => act(async () => {
        findInShadowRoots(container, "textarea")!.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter", bubbles: true, composed: true, cancelable: true,
        }));
      });
      await pressEnter();
      expect(onSend).toHaveBeenCalledTimes(2);

      await render(false);
      expect(button().disabled).toBe(true);
      await act(async () => button().click());
      await pressEnter();
      expect(onSend).toHaveBeenCalledTimes(2);
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it("keeps blocked text unsent and preserves the stop action while loading", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const onSend = vi.fn();
    const onStop = vi.fn();
    const render = async (blocked: boolean, loading = false) => act(async () => {
      root.render(createElement(ChatSenderBox, {
        placement: "bottom", value: "hello", loading, onSend, onStop,
        sendBtnDisabled: () => blocked,
      }));
    });
    const button = () => findInShadowRoots(container, "button") as HTMLButtonElement;
    try {
      await render(true);
      expect(button().disabled).toBe(true);
      await act(async () => button().click());
      expect(onSend).not.toHaveBeenCalled();
      await render(false);
      expect(button().disabled).toBe(false);
      await act(async () => button().click());
      expect(onSend).toHaveBeenCalledOnce();
      expect(onSend.mock.calls[0][0].detail.value).toBe("hello");
      await render(true, true);
      expect(button().disabled).toBe(false);
      await act(async () => button().click());
      expect(onStop).toHaveBeenCalledOnce();
      expect(onSend).toHaveBeenCalledOnce();
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
