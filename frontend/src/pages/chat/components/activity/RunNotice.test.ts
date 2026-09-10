// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { Button } from "@/components/ui";
import { RunNotice } from "./RunNotice";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("keeps system feedback literal and its recovery action usable", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const recover = vi.fn();
  const message = "连接中断\n**原始错误** <script>example</script>";
  try {
    await act(async () => root.render(createElement(RunNotice, { message },
      createElement(Button, { onClick: recover }, "继续整理"))));
    const notice = container.querySelector('[role="status"]');
    expect(notice?.textContent).toBe(`${message}继续整理`);
    expect(notice?.querySelector("script, strong, t-chat-message")).toBeNull();
    await act(async () => container.querySelector("button")?.click());
    expect(recover).toHaveBeenCalledOnce();
    await act(async () => root.render(createElement(RunNotice, { message }, false, null)));
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector('[data-slot="alert-description"]')?.children).toHaveLength(1);
  } finally {
    await act(async () => root.unmount());
  }
});
