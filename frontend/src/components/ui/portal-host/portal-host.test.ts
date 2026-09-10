// @vitest-environment jsdom
import { Activity, StrictMode, act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { createPortal } from "react-dom";
import { PortalHost } from "./index";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "../dropdown-menu";
import { Popover, PopoverTrigger, PopoverContent } from "../popover";
import { Tooltip, TooltipProvider, TooltipTrigger, TooltipContent } from "../tooltip";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "../dialog";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "../sheet";
import { Select, SelectTrigger, SelectContent, SelectItem } from "../select";
import { ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem } from "../context-menu";
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
globalThis.ResizeObserver = class {
  observe() { }
  unobserve() { }
  disconnect() { }
};
HTMLElement.prototype.scrollIntoView = () => { };
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  expect(document.querySelector("[data-ui-portal-host]")).toBeNull();
  expect(document.body.style.pointerEvents).not.toBe("none");
});
const cases: [string, ReactNode][] = [
  ["dropdown-menu", createElement(DropdownMenu, { defaultOpen: true },
      createElement(DropdownMenuTrigger, null, "open"),
      createElement(DropdownMenuContent, null,
        createElement(DropdownMenuItem, null, "item")))],
  ["popover", createElement(Popover, { defaultOpen: true },
      createElement(PopoverTrigger, null, "open"),
      createElement(PopoverContent, null,
        createElement("input", { defaultValue: "draft" })))],
  ["tooltip", createElement(TooltipProvider, null,
      createElement(Tooltip, { open: true },
        createElement(TooltipTrigger, null, "open"),
        createElement(TooltipContent, null, "tip")))],
  ["dialog", createElement(Dialog, { defaultOpen: true },
      createElement(DialogContent, null,
        createElement(DialogTitle, null, "title"),
        createElement(DialogDescription, null, "description")))],
  ["sheet", createElement(Sheet, { defaultOpen: true },
      createElement(SheetContent, null,
        createElement(SheetTitle, null, "title"),
        createElement(SheetDescription, null, "description")))],
  ["select", createElement(Select, { defaultOpen: true },
      createElement(SelectTrigger, null),
      createElement(SelectContent, null,
        createElement(SelectItem, { value: "a" }, "a")))],
  ["context-menu", createElement(ContextMenu, null,
      createElement(ContextMenuTrigger, null, "open"),
      createElement(ContextMenuContent, null,
        createElement(ContextMenuItem, null, "item")))],
];
it.each(cases)("%s detaches on Activity hide and restores without leaking or losing content", async (name, children) => {
  const render = async (mode: "visible" | "hidden") => {
    await act(async () => root.render(createElement(Activity, {
      mode, children: createElement("section", null, children),
    })));
  };
  await render("visible");
  if (name === "context-menu") {
    await act(async () => container.querySelector('[data-slot="context-menu-trigger"]')!
      .dispatchEvent(new MouseEvent("contextmenu", { bubbles: true })));
  }
  const selector = `[data-slot="${name}-content"]`;
  expect(document.querySelector(selector)).not.toBeNull();
  const field = document.querySelector<HTMLInputElement>(`${selector} input`);
  if (field)
    field.value = "unsaved change";
  for (let cycle = 0; cycle < 2; cycle++) {
    await render("hidden");
    expect(document.querySelector(selector)).toBeNull();
    expect(document.body.style.pointerEvents).not.toBe("none");
    await render("visible");
    expect(document.querySelector(selector)).not.toBeNull();
    if (field)
      expect(document.querySelector<HTMLInputElement>(`${selector} input`)?.value).toBe("unsaved change");
  }
});
it("keeps a custom container and StrictMode setup/cleanup balanced", async () => {
  const target = document.createElement("div");
  document.body.append(target);
  try {
    await act(async () => root.render(createElement(StrictMode, null,
      createElement(PortalHost, { container: target,
        children: (host: HTMLDivElement) => createPortal(createElement("span", null, "custom"), host),
      }))));
    expect(target.querySelectorAll("[data-ui-portal-host]")).toHaveLength(1);
    expect(target.textContent).toBe("custom");
    await act(async () => root.render(null));
    expect(target.childElementCount).toBe(0);
  }
  finally {
    target.remove();
  }
});
