// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ToolActivity } from "./ToolActivity";

vi.mock("./AttachmentImageViewer", () => ({
  AttachmentImageViewer: ({ preview }: { preview: { name: string } | null }) => (
    preview ? createElement("div", { role: "dialog" }, preview.name) : null
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("uses the ordinary tool disclosure, opens the existing viewer and disables unavailable images", async () => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const onDisclosureChange = vi.fn();
  try {
    await act(async () => root.render(createElement(ToolActivity, {
      blocks: [{ kind: "tool", toolCallId: "image", name: "view_image", args: "", status: "done",
        images: [{ assetId: "a".repeat(64), kind: "image", mediaType: "image/png", displayName: "screen.png", sizeBytes: 80 }],
      }],
      onDisclosureChange,
    })));
    expect(host.textContent).toContain("已查看一张图片");
    expect(host.querySelector("img")).toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
    expect(onDisclosureChange).toHaveBeenCalledOnce();
    expect(host.querySelectorAll('[data-slot="collapsible-trigger"]')).toHaveLength(1);
    expect(host.querySelector('[aria-label="结果"] img')).not.toBeNull();
    expect(host.querySelector("img")?.getAttribute("src")).toContain("a".repeat(64));
    await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="放大查看 screen.png"]')!.click());
    expect(host.querySelector('[role="dialog"]')?.textContent).toBe("screen.png");
    await act(async () => host.querySelector("img")!.dispatchEvent(new Event("error")));
    expect(host.textContent).toContain("图片不可用");
    expect(host.querySelector('[aria-label="放大查看 screen.png"]')).toBeNull();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  } finally {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  }
});
