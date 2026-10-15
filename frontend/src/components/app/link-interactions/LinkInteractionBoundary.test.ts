// @vitest-environment jsdom
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinkInteractionBoundary } from "./LinkInteractionBoundary";
import { activateLink, resolveLink, type ResolvedLink } from "@/lib/links/actions";
import { managedLinkHref } from "@/lib/links/targets";
import { Button, ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem } from "@/components/ui";

vi.mock("@/lib/links/actions", () => ({
  resolveLink: vi.fn(), activateLink: vi.fn(), copyLink: vi.fn(),
  linkActionLabel: () => "打开所在文件夹",
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("shared link interactions", () => {
  let host: HTMLDivElement;
  let root: Root;
  const render = async (sessionId: string, children: ReactNode = createElement("a", { href: managedLinkHref("C:/target.txt") }, "https://wrong.example")) => {
    await act(async () => root.render(createElement(LinkInteractionBoundary, { context: { kind: "session", sessionId }, children })));
  };
  beforeEach(() => {
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    vi.mocked(resolveLink).mockResolvedValue({ kind: "file", value: "C:/target.txt" });
    vi.mocked(activateLink).mockResolvedValue();
  });
  afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.clearAllMocks(); });
  it("uses the actual href and source session, never visible link text", async () => {
    await render("one");
    await act(async () => host.querySelector("a")!.click());
    expect(resolveLink).toHaveBeenCalledWith(managedLinkHref("C:/target.txt"), { kind: "session", sessionId: "one" });
    expect(activateLink).toHaveBeenCalledTimes(1);
  });
  it("cancels an outstanding click when the source session changes", async () => {
    let finish!: (value: ResolvedLink) => void;
    vi.mocked(resolveLink).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await render("one");
    await act(async () => host.querySelector("a")!.click());
    await render("two");
    await act(async () => finish({ kind: "file", value: "C:/old.txt" }));
    expect(activateLink).not.toHaveBeenCalled();
  });
  it("finds links through a real shadow root", async () => {
    await render("one", createElement("div", { id: "shadow-host" }));
    const shadow = host.querySelector("#shadow-host")!.attachShadow({ mode: "open" });
    const link = document.createElement("a"); link.href = managedLinkHref("C:/shadow.txt"); shadow.append(link);
    await act(async () => link.dispatchEvent(new MouseEvent("click", { bubbles: true, composed: true, cancelable: true })));
    expect(resolveLink).toHaveBeenCalledWith(link.getAttribute("href"), { kind: "session", sessionId: "one" });
  });
  it("opens the shared menu and activates its action after the menu closes", async () => {
    await render("one");
    await act(async () => host.querySelector("a")!.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2 })));
    const action = Array.from(document.querySelectorAll('[role="menuitem"]')).find(node => node.textContent === "打开所在文件夹");
    expect(action).toBeTruthy();
    await act(async () => (action as HTMLElement).click());
    expect(activateLink).toHaveBeenCalledTimes(1);
  });
  it("leaves image preview and attachment removal to their existing handlers", async () => {
    await render("one", createElement("div", { id: "cards" }));
    const card = document.createElement("t-filecard") as HTMLElement & { props: unknown };
    card.props = { item: { url: "http://localhost/image", fileType: "image" } };
    const remove = document.createElement("div"); remove.className = "t-filecard-remove"; card.append(remove);
    host.querySelector("#cards")!.append(card);
    await act(async () => { card.click(); remove.click(); });
    expect(resolveLink).not.toHaveBeenCalled();
  });
  it("opens the same menu by keyboard and closes it when the target disappears", async () => {
    await render("one");
    const link = host.querySelector("a")!;
    await act(async () => link.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", shiftKey: true, bubbles: true, cancelable: true })));
    expect(document.querySelector('[role="menu"]')).toBeTruthy();
    await act(async () => link.remove());
    expect(document.querySelector('[role="menu"]')).toBeNull();
    expect(activateLink).not.toHaveBeenCalled();
  });
  it("does not suppress an existing nested product menu", async () => {
    await render("one", createElement(ContextMenu, {},
      createElement(ContextMenuTrigger, {}, "会话"),
      createElement(ContextMenuContent, {}, createElement(ContextMenuItem, {}, "原有操作"))));
    const trigger = host.querySelector('[data-context-menu-policy="custom"]')!;
    await act(async () => trigger.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2 })));
    expect(document.querySelector('[role="menu"]')?.textContent).toBe("原有操作");
    expect(resolveLink).not.toHaveBeenCalled();
  });
  it("supports the image keyboard menu and invalidates it when its asset changes", async () => {
    const imageProps = { "data-attachment-url": "http://localhost/first", children: "image" };
    await render("one", createElement(Button, imageProps));
    const image = host.querySelector("button")!;
    await act(async () => image.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", shiftKey: true, bubbles: true, cancelable: true })));
    expect(resolveLink).toHaveBeenCalledWith("http://localhost/first", { kind: "session", sessionId: "one" });
    expect(document.querySelector('[role="menu"]')).toBeTruthy();
    await act(async () => image.setAttribute("data-attachment-url", "http://localhost/second"));
    expect(document.querySelector('[role="menu"]')).toBeNull();
    expect(activateLink).not.toHaveBeenCalled();
  });
});
