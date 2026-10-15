// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { GeneratedImage } from "./GeneratedImage";
import { LinkInteractionBoundary } from "@/components/app/link-interactions/LinkInteractionBoundary";
import { activateLink, resolveLink } from "@/lib/links/actions";
import type { ImageBlock } from "@/types/chat";

vi.mock("@/services", () => ({ attachmentAssetUrl: (id: string) => `http://localhost/v1/attachments/assets/${id}/content` }));
vi.mock("@/lib/links/actions", () => ({
  resolveLink: vi.fn(), activateLink: vi.fn(), copyLink: vi.fn(),
  linkActionLabel: () => "打开所在文件夹",
}));
vi.mock("./AttachmentImageViewer", () => ({
  AttachmentImageViewer: ({ preview }: { preview: { url: string } | null }) => preview
    ? createElement("div", { role: "dialog", "data-preview-url": preview.url }) : null,
}));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const block: ImageBlock = { kind: "image", mid: "image", itemIndex: 0, status: "saved",
  images: ["a", "b"].map(id => ({ assetId: id.repeat(64), kind: "image", mediaType: "image/png", displayName: `generated-${id}.png`, sizeBytes: 80 })),
};

it("keeps preview on left click and uses the shared file menu for the specific image", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  vi.mocked(resolveLink).mockResolvedValue({ kind: "file", value: "C:/assets/original" });
  vi.mocked(activateLink).mockResolvedValue();
  try {
    await act(async () => root.render(createElement(LinkInteractionBoundary, {
      context: { kind: "session", sessionId: "source" },
      children: createElement(GeneratedImage, { block, running: false }),
    })));
    expect(host.textContent).not.toContain("generated-");
    expect(host.textContent).not.toContain("保存原图");
    expect(host.querySelectorAll("button")).toHaveLength(2);
    await act(async () => host.querySelector("button")!.click());
    expect(host.querySelector('[role="dialog"]')?.getAttribute("data-preview-url")).toContain("a".repeat(64));
    expect(activateLink).not.toHaveBeenCalled();
    expect(resolveLink).not.toHaveBeenCalled();
    const secondImage = host.querySelectorAll("img")[1];
    await act(async () => secondImage.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2 })));
    expect(resolveLink).toHaveBeenCalledWith(secondImage.getAttribute("src"), { kind: "session", sessionId: "source" });
    const action = Array.from(document.querySelectorAll('[role="menuitem"]')).find(node => node.textContent === "打开所在文件夹");
    expect(action).toBeTruthy();
    expect(document.querySelector('[role="menu"]')?.textContent).toContain("复制完整路径");
    await act(async () => (action as HTMLElement).click());
    expect(activateLink).toHaveBeenCalledWith({ kind: "file", value: "C:/assets/original" }, expect.anything());
  } finally {
    await act(async () => root.unmount());
    host.remove();
    vi.clearAllMocks();
  }
});

it("keeps unavailable and incomplete image feedback without file actions", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(GeneratedImage, { block: { ...block, images: block.images.slice(0, 1) }, running: false })));
    await act(async () => host.querySelector("img")!.dispatchEvent(new Event("error")));
    expect(host.textContent).toContain("图片不可用");
    expect(host.querySelector("[data-attachment-url]")).toBeNull();
    for (const status of ["pending", "failed"] as const) {
      await act(async () => root.render(createElement(GeneratedImage, { block: { ...block, status }, running: false })));
      expect(host.querySelector("button")).toBeNull();
      expect(host.querySelector('[role="status"]')).not.toBeNull();
    }
  } finally {
    await act(async () => root.unmount());
  }
});