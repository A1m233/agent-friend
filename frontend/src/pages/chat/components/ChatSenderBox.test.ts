// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { ChatSenderBox } from "./ChatSenderBox";

// Model the installed sender's shared Enter/button dispatch, keeping native event propagation.
vi.mock("@tdesign-react/chat", async () => {
  const { createElement: h, forwardRef } = await import("react");
  return {
    ChatSender: forwardRef<HTMLDivElement, {
      loading?: boolean; onSend?: () => void; onStop?: () => void;
    }>((props, ref) => {
      const dispatch = () => props.loading ? props.onStop?.() : props.onSend?.();
      return h("div", { ref, onKeyDown: (event) => {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); dispatch(); }
      } }, h("textarea"), h("button", { onClick: dispatch }, "action"));
    }),
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("ChatSenderBox keyboard ownership", () => {
  it.each([true, false])("keeps Enter as draft input while busy, with stop available=%s", async (loading) => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const onSend = vi.fn();
    const onStop = vi.fn();
    try {
      await act(async () => root.render(createElement(ChatSenderBox, {
        placement: "bottom", loading, sendBlocked: true, onSend, onStop,
      })));
      const enter = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
      await act(async () => { container.querySelector("textarea")!.dispatchEvent(enter); });
      expect(enter.defaultPrevented).toBe(false);
      expect(onSend).not.toHaveBeenCalled();
      expect(onStop).not.toHaveBeenCalled();
      if (loading) {
        await act(async () => container.querySelector("button")!.click());
        expect(onStop).toHaveBeenCalledTimes(1);
      }
      await act(async () => root.render(createElement(ChatSenderBox, {
        placement: "bottom", loading: false, sendBlocked: false, onSend, onStop,
      })));
      await act(async () => { container.querySelector("textarea")!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true, isComposing: true }),
      ); });
      expect(onSend).not.toHaveBeenCalled();
      await act(async () => { container.querySelector("textarea")!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      ); });
      expect(onSend).toHaveBeenCalledTimes(1);
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
