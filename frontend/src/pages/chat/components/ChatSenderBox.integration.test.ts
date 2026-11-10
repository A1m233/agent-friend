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
  it("explains a blocked send on Enter or focus without consuming IME or Shift+Enter", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const onSend = vi.fn();
    const onBlockedSend = vi.fn();
    const reason = "会话正在准备，暂时无法发送";
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    try {
      await act(async () => root.render(createElement(ChatSenderBox, {
        placement: "bottom", value: "草稿", sendBlocked: true, sendBtnDisabled: true,
        blockedSendReason: reason, onBlockedSend, onSend,
      })));
      const textarea = findInShadowRoots(container, "textarea") as HTMLTextAreaElement;
      const enter = (options: KeyboardEventInit = {}) => textarea.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter", bubbles: true, composed: true, cancelable: true, ...options,
      }));
      expect(enter({ isComposing: true })).toBe(true);
      expect(enter({ shiftKey: true })).toBe(true);
      expect(onBlockedSend).not.toHaveBeenCalled();
      expect(enter()).toBe(false);
      expect(enter({ repeat: true })).toBe(false);
      expect(onBlockedSend).toHaveBeenCalledOnce();
      expect(textarea.value).toBe("草稿");

      const button = container.querySelector<HTMLButtonElement>('[slot="actions"] button')!;
      expect(button.getAttribute("aria-disabled")).toBe("true");
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
      await act(async () => button.focus());
      expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(reason);
      await act(async () => button.blur());
      await act(async () => {
        button.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, pointerType: "mouse" }));
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
      expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(reason);
      await act(async () => button.click());
      expect(onSend).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.unmount());
      container.remove();
      vi.unstubAllGlobals();
    }
  });

  it("observes the native stop click before dispatch, without breaking stop when the observer fails", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const order: string[] = [];
    const onSend = vi.fn();
    const onStop = vi.fn(() => order.push("stop"));
    const observe = vi.fn(() => { order.push("click"); throw new Error("diagnostic failure"); });
    const render = (loading: boolean) => act(async () => root.render(createElement(ChatSenderBox, {
      placement: "bottom", loading, value: "hello", onSend, onStop, onNativeStopClick: observe,
    })));
    try {
      await render(true);
      await act(async () => (findInShadowRoots(container, "button") as HTMLButtonElement).click());
      expect(order).toEqual(["click", "stop"]);
      expect(onStop).toHaveBeenCalledOnce();
      expect(onSend).not.toHaveBeenCalled();
      await render(false);
      await act(async () => (findInShadowRoots(container, "button") as HTMLButtonElement).click());
      expect(observe).toHaveBeenCalledOnce();
      expect(onSend).toHaveBeenCalledOnce();
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

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
