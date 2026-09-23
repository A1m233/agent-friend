// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ExecutionDisclosure, ExecutionDisclosureStateProvider } from "./ExecutionDisclosure";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("starts open, collapses on completion, respects manual state and reveals a search target", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const onDisclosureChange = vi.fn();
  const render = async (collapse: boolean, revealTarget?: string) => {
    await act(async () => root.render(createElement(ExecutionDisclosure, {
      enabled: true, label: "用时 1秒", collapse, revealTarget, onDisclosureChange,
      children: (open: boolean) => open ? "progress text" : null,
    })));
  };
  try {
    await render(false);
    expect(container.querySelector("button")?.getAttribute("aria-expanded")).toBe("true");
    await render(true);
    expect(container.textContent).not.toContain("progress text");
    await act(async () => container.querySelector("button")?.click());
    expect(container.textContent).toContain("progress text");
    await render(true);
    expect(container.textContent).toContain("progress text");
    await act(async () => container.querySelector("button")?.click());
    await render(true, "source-a");
    expect(container.textContent).toContain("progress text");
    await render(true);
    expect(container.textContent).not.toContain("progress text");
    expect(onDisclosureChange).toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});

it("keeps active work open without a control and collapses when interaction becomes available", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (interactive: boolean, revealTarget?: string) => act(async () => root.render(
    createElement(ExecutionDisclosure, { enabled: true, interactive, collapse: true,
      label: "Status", revealTarget, children: (open: boolean) => open ? "process" : null }),
  ));
  try {
    await render(false, "search");
    expect(container.querySelector("button")).toBeNull();
    expect(container.textContent).toContain("process");
    await render(false);
    await render(true);
    expect(container.textContent).not.toContain("process");
    await act(async () => container.querySelector("button")?.click());
    expect(container.textContent).toContain("process");
    await render(false);
    await render(true);
    expect(container.textContent).not.toContain("process");
  } finally { await act(async () => root.unmount()); }
});

it("does not persist a search reveal across virtualized remounts", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (mounted: boolean, revealTarget?: string) => act(async () => root.render(
    createElement(ExecutionDisclosureStateProvider, { children: mounted
      ? createElement(ExecutionDisclosure, { disclosureId: "run", enabled: true, collapse: true,
        label: "Status", revealTarget, children: (open: boolean) => open ? "process" : null }) : null }),
  ));
  try {
    await render(true, "search");
    expect(container.textContent).toContain("process");
    await render(false);
    await render(true);
    expect(container.textContent).not.toContain("process");
    await act(async () => container.querySelector("button")?.click());
    await render(false);
    await render(true);
    expect(container.textContent).toContain("process");
  } finally { await act(async () => root.unmount()); }
});

it("has one control for all process sections and no control for unsupported runs", async () => {
  const container = document.createElement("div");
  const root = createRoot(container);
  const render = async (enabled: boolean) => act(async () => root.render(createElement(ExecutionDisclosure, {
    enabled, label: "用时 2秒", collapse: true,
    children: (open: boolean) => [open && "process one", "unknown stays", open && "process two", "final stays"],
  })));
  try {
    await render(true);
    expect(container.querySelectorAll("button")).toHaveLength(1);
    expect(container.querySelector("button")?.classList.contains("px-0.5")).toBe(true);
    expect(container.querySelector("button")?.classList.contains("has-[>svg]:px-0.5")).toBe(true);
    expect(container.querySelector("button")?.classList.contains("has-[>svg]:px-2.5")).toBe(false);
    expect(container.querySelector("button")?.classList.contains("text-sm")).toBe(true);
    expect(container.querySelector("button")?.classList.contains("hover:bg-transparent")).toBe(true);
    expect(container.querySelector("button")?.classList.contains("hover:text-muted")).toBe(true);
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
    expect(container.querySelector<HTMLElement>("[data-run-separator]")?.style.height).toBe("var(--size-chat-run-divider)");
    expect(container.textContent).toContain("unknown stays");
    expect(container.textContent).toContain("final stays");
    expect(container.textContent).not.toContain("process one");
    await act(async () => container.querySelector("button")?.click());
    expect(container.textContent).toContain("process one");
    expect(container.textContent).toContain("process two");
    await render(false);
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector("[data-run-status-line]")?.classList.contains("px-0.5")).toBe(true);
    expect(container.querySelector("[data-run-status-line]")?.classList.contains("text-sm")).toBe(true);
    expect(container.querySelectorAll("[data-run-separator]")).toHaveLength(1);
    expect(container.textContent).toContain("用时 2秒");
    expect(container.textContent).toContain("process one");
  } finally { await act(async () => root.unmount()); }
});
