// @vitest-environment jsdom
import { act, createElement, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { computeAccessoryLayout } from "@/pet/accessoryLayout/geometry";
import { actionBarMetrics, actionBarMinimumMetrics } from "@/pet/accessoryLayout/actionBarMetrics";
import { ActionBar } from "./ActionBar";

function props(): ComponentProps<typeof ActionBar> {
  const noop = () => {};
  return {
    accessoryLayout: {
      side: "top",
      actionBar: { orientation: "horizontal", left: 300, top: 300, width: 300, height: 44 },
      bubble: null, bubbleLimit: { w: 360, h: 480 }, tooltipSide: "left",
      tooltipBoundary: { x: 0, y: 0, w: 1920, h: 1080 },
    },
    actionBarAnchor: { x: 400, y: 352, w: 100, h: 100 },
    viewport: { x: 0, y: 0, w: 1920, h: 1080 },
    petAvatarSize: "normal", voiceCallActive: false, passthroughDisabled: false,
    memoryInspectorEnabled: true,
    onMouseEnter: noop, onMouseLeave: noop, onOpenChat: noop,
    onCollapseToEdge: noop, onRestoreNormal: noop, onOpenSettings: noop,
    onOpenFeedback: noop, onOpenIMConnect: noop, onStartVoiceCall: noop,
    onTogglePassthrough: noop, onOpenMemoryInspector: noop, onOpenLive2DDebugger: noop,
    onInjectShort: noop, onInjectLong: noop, onInjectReminder: noop,
    onClearDebugNotifications: noop,
  };
}

describe("ActionBar notification debug controls", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("keeps four explicit, accessible debug actions together for manual notification checks", () => {
    vi.stubEnv("DEV", true);
    const markup = renderToStaticMarkup(createElement(ActionBar, props()));
    expect([...markup.matchAll(/data-notification-debug="([^"]+)"/g)].map((match) => match[1]))
      .toEqual(["short", "long", "stack", "clear"]);
    expect(markup).toContain('aria-label="调试短通知（不调用模型）"');
    expect(markup).toContain('aria-label="清空调试通知（不影响真实通知）"');
    expect(markup).toContain('aria-label="下一页"');
  });

  it("does not render notification debug actions in production", () => {
    vi.stubEnv("DEV", false);
    const markup = renderToStaticMarkup(createElement(ActionBar, props()));
    expect(markup).not.toContain("data-notification-debug");
    expect(markup).not.toContain("清空调试通知");
    expect(markup).toContain('aria-label="打开对话"');
  });
});

describe("ActionBar outward tooltips with real Radix DOM", () => {
  let root: Root;
  let container: HTMLDivElement;
  let triggerRect: DOMRect;
  let naturalSize: { w: number; h: number };

  beforeEach(() => {
    vi.stubEnv("DEV", false);
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("IntersectionObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(performance.now()), 16));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    triggerRect = new DOMRect(8, 64, 32, 32);
    naturalSize = { w: 180, h: 28 };
    const content = (element: HTMLElement) => element.matches("[data-slot=tooltip-content]")
      ? element : element.matches("[data-radix-popper-content-wrapper]")
        ? element.querySelector<HTMLElement>("[data-slot=tooltip-content]") : null;
    const width = (element: HTMLElement) => content(element)
      ? Math.min(naturalSize.w, Number.parseFloat(content(element)!.style.maxWidth)) : 10;
    const height = (element: HTMLElement) => content(element)
      ? Math.min(naturalSize.h, Number.parseFloat(content(element)!.style.maxHeight)) : 10;
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) { return width(this); });
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) { return height(this); });
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.matches("[data-slot=tooltip-content]") ? naturalSize.h : 10;
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.matches("button, [data-slot=tooltip-trigger]")) return triggerRect;
      if (this === document.documentElement) return new DOMRect(0, 0, 400, 400);
      return new DOMRect(0, 0, width(this), height(this));
    });
    vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(400);
    vi.spyOn(document.documentElement, "clientHeight", "get").mockReturnValue(400);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
    vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks();
    vi.unstubAllGlobals(); vi.unstubAllEnvs();
  });

  async function openTooltip(input: ComponentProps<typeof ActionBar>, label = "打开对话") {
    await act(async () => root.render(createElement(ActionBar, input)));
    const button = container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
    const trigger = button.closest<HTMLElement>("[data-slot=tooltip-trigger]")!;
    await act(async () => {
      const move = new MouseEvent("pointermove", { bubbles: true });
      Object.defineProperty(move, "pointerType", { value: "mouse" });
      trigger.dispatchEvent(move);
      await vi.advanceTimersByTimeAsync(600);
    });
    const tooltip = document.querySelector<HTMLElement>("[data-slot=tooltip-content]");
    expect(tooltip).not.toBeNull();
    return tooltip!;
  }

  it.each([
    { side: "top" as const, trigger: [8, 64], boundary: { x: 8, y: 8, w: 384, h: 48 }, expected: [8, 28], size: { w: 180, h: 28 } },
    { side: "top" as const, trigger: [360, 64], boundary: { x: 8, y: 8, w: 384, h: 48 }, expected: [212, 28], size: { w: 180, h: 28 } },
    { side: "bottom" as const, trigger: [8, 200], boundary: { x: 8, y: 240, w: 384, h: 48 }, expected: [8, 240], size: { w: 180, h: 28 } },
    { side: "bottom" as const, trigger: [360, 200], boundary: { x: 8, y: 240, w: 384, h: 48 }, expected: [212, 240], size: { w: 180, h: 28 } },
    { side: "left" as const, trigger: [200, 8], boundary: { x: 64, y: 8, w: 128, h: 384 }, expected: [64, 8], size: { w: 180, h: 60 } },
    { side: "right" as const, trigger: [200, 360], boundary: { x: 240, y: 8, w: 128, h: 384 }, expected: [240, 332], size: { w: 180, h: 60 } },
  ])("keeps $side facing outward and shifts inside the corridor at $trigger", async ({ side, trigger, boundary, expected, size }) => {
    triggerRect = new DOMRect(trigger[0], trigger[1], 32, 32);
    naturalSize = size;
    const input = props();
    input.memoryInspectorEnabled = false;
    input.accessoryLayout = { ...input.accessoryLayout!, side, tooltipSide: "bottom", tooltipBoundary: boundary };
    if (side === "bottom") input.accessoryLayout.bubble = { x: 8, y: 296, w: 360, h: 96 };
    const tooltip = await openTooltip(input);
    expect(tooltip.dataset.side).toBe(side);
    expect(tooltip.style.maxWidth).toBe(`${Math.min(boundary.w, 256)}px`);
    expect(tooltip.style.maxHeight).toBe(`${boundary.h}px`);
    expect(tooltip.className).toContain("wrap-anywhere");
    expect(tooltip.parentElement?.style.transform).toBe(`translate(${expected[0]}px, ${expected[1]}px)`);
    if (input.accessoryLayout.bubble) {
      expect(expected[1] + tooltip.offsetHeight).toBeLessThan(input.accessoryLayout.bubble.y);
    }
  });

  it("omits tooltip content when there is no readable corridor but keeps controls accessible and clickable", async () => {
    const input = props();
    input.accessoryLayout!.tooltipBoundary = null;
    input.onOpenChat = vi.fn();
    await act(async () => root.render(createElement(ActionBar, input)));
    const button = container.querySelector<HTMLButtonElement>('button[aria-label="打开对话"]')!;

    await act(async () => {
      button.focus();
      const move = new MouseEvent("pointermove", { bubbles: true });
      Object.defineProperty(move, "pointerType", { value: "mouse" });
      button.dispatchEvent(move);
      await vi.advanceTimersByTimeAsync(1000);
      button.click();
    });

    expect(input.onOpenChat).toHaveBeenCalledOnce();
    expect(button.dataset.size).toBe("icon-sm");
    expect(button.dataset.variant).toBe("ghost");
    expect(button.hasAttribute("data-hit")).toBe(true);
    expect(button.hasAttribute("aria-describedby")).toBe(false);
    expect(container.querySelector("[data-slot=tooltip-trigger]")).toBeNull();
    expect(document.querySelector("[data-slot=tooltip-content]")).toBeNull();
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="上一页"]')!.disabled).toBe(true);
  });

  it("removes an already open tooltip when its corridor disappears", async () => {
    const input = props();
    input.onOpenChat = vi.fn();
    await openTooltip(input);

    input.accessoryLayout = { ...input.accessoryLayout!, tooltipBoundary: null };
    await act(async () => root.render(createElement(ActionBar, input)));
    expect(document.querySelector("[data-slot=tooltip-content]")).toBeNull();
    const button = container.querySelector<HTMLButtonElement>('button[aria-label="打开对话"]')!;
    await act(async () => button.click());
    expect(input.onOpenChat).toHaveBeenCalledOnce();
  });

  it("updates an open tooltip when the ActionBar moves from above to below the pet", async () => {
    const input = props();
    input.memoryInspectorEnabled = false;
    input.accessoryLayout!.tooltipBoundary = { x: 8, y: 8, w: 384, h: 48 };
    const tooltip = await openTooltip(input);
    expect(tooltip.dataset.side).toBe("top");

    triggerRect = new DOMRect(360, 200, 32, 32);
    input.accessoryLayout = {
      ...input.accessoryLayout!, side: "bottom", tooltipSide: "top",
      tooltipBoundary: { x: 8, y: 240, w: 384, h: 48 },
      bubble: { x: 8, y: 296, w: 360, h: 96 },
    };
    await act(async () => root.render(createElement(ActionBar, input)));

    expect(tooltip.dataset.side).toBe("bottom");
    expect(tooltip.parentElement?.style.transform).toBe("translate(212px, 240px)");
  });

  it.each([
    { name: "top center without a bubble", anchor: { x: 880, y: 0, w: 160, h: 160 }, bubble: null, side: "bottom", vertical: false },
    { name: "top center with a bubble", anchor: { x: 880, y: 0, w: 160, h: 160 }, bubble: { w: 360, h: 240 }, side: "bottom", vertical: false },
    { name: "center with a tall bubble queue", anchor: { x: 880, y: 460, w: 160, h: 160 }, bubble: { w: 360, h: 1800 }, side: "top", vertical: false },
    { name: "left screen edge", anchor: { x: 0, y: 460, w: 80, h: 100 }, bubble: { w: 360, h: 240 }, side: "right", vertical: true },
    { name: "right screen edge", anchor: { x: 1840, y: 460, w: 80, h: 100 }, bubble: { w: 360, h: 240 }, side: "left", vertical: true },
  ])("renders the computed $name layout and outward tooltip", async ({ anchor, bubble, side, vertical }) => {
    const input = props();
    input.memoryInspectorEnabled = false;
    input.actionBarAnchor = anchor;
    input.viewport = { x: 0, y: 0, w: 1920, h: 1040 };
    input.accessoryLayout = computeAccessoryLayout({
      anchor,
      workArea: input.viewport,
      sizes: actionBarMetrics(6, 4),
      minimumSizes: actionBarMinimumMetrics(6, 4),
      bubble,
      minimumBubbleSize: { w: 192, h: 64 },
      gap: 8,
    });
    expect(input.accessoryLayout?.side).toBe(side);
    const layout = input.accessoryLayout!.actionBar;
    triggerRect = new DOMRect(layout.left + 8, layout.top + 6, 32, 32);

    const tooltip = await openTooltip(input);
    const bar = container.querySelector<HTMLElement>("div[data-hit][style]")!;
    expect(bar.classList.contains("flex-col")).toBe(vertical);
    expect(bar.style.left).toBe(`${layout.left}px`);
    expect(bar.style.top).toBe(`${layout.top}px`);
    expect(tooltip.dataset.side).toBe(side);
    expect(tooltip.style.maxHeight).toBe(`${input.accessoryLayout!.tooltipBoundary!.h}px`);
    if (side === "bottom") {
      const boundary = input.accessoryLayout!.tooltipBoundary!;
      expect(tooltip.parentElement?.style.transform).toContain(`, ${boundary.y}px)`);
      if (input.accessoryLayout!.bubble) {
        expect(boundary.y + tooltip.offsetHeight).toBeLessThan(input.accessoryLayout!.bubble.y);
      }
    }
  });

  it("scrolls an exceptionally short corridor without flipping into the pet", async () => {
    naturalSize = { w: 180, h: 72 };
    const input = props();
    input.memoryInspectorEnabled = false;
    input.accessoryLayout!.tooltipBoundary = { x: 8, y: 8, w: 384, h: 32 };
    const tooltip = await openTooltip(input, "缩小桌宠并移动到边上");
    expect(tooltip.dataset.side).toBe("top");
    expect(tooltip.style.maxHeight).toBe("32px");
    expect(tooltip.style.overflowY).toBe("auto");
    expect(tooltip.parentElement?.style.transform).toBe("translate(8px, 8px)");
  });

  it("uses the same outward rule for disabled carousel arrows", async () => {
    const input = props();
    input.accessoryLayout!.side = "right";
    input.accessoryLayout!.tooltipSide = "top";
    input.accessoryLayout!.tooltipBoundary = { x: 240, y: 8, w: 128, h: 384 };
    triggerRect = new DOMRect(200, 8, 32, 32);
    const tooltip = await openTooltip(input, "上一页");
    expect(tooltip.dataset.side).toBe("right");
    expect(tooltip.parentElement?.style.transform).toBe("translate(240px, 10px)");
  });
});
