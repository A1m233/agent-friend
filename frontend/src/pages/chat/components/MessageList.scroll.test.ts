// @vitest-environment jsdom

import { act, createElement, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConversationStore } from "@/stores";
import { useSocialAuthRequestsStore } from "@/stores/socialAuthRequests";
import type { SocialAuthRequestSnapshot, SocialAuthRequestStatus } from "@/services";
import type { ChatMessage, ChatTextReference } from "@/types/chat";
import { MessageList, MessageTimeline } from "./MessageList";

// Rendering Markdown in a custom element is unrelated to scroll ownership.
// The timeline, actual disclosure controls, ScrollArea and virtualizer stay real.
vi.mock("@tdesign-react/chat", () => ({
  ChatMessage: ({ children, content }: {
    children?: ReactNode;
    content?: Array<{ data?: unknown }>;
  }) => createElement("div", null,
    content?.map((item) => typeof item.data === "string" ? item.data : "").join(""), children),
}));
vi.mock("./MessageAttachments", () => ({ MessageAttachments: () => null }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const VIEWPORT_HEIGHT = 400;
const ROW_HEIGHT = 180;
const EXPANDED_ROW_HEIGHT = 520;

function messages(prefix = "session-a"): ChatMessage[] {
  return Array.from({ length: 6 }, (_, index) => ({
    id: `${prefix}-${index}`,
    role: "assistant",
    status: "complete",
    createdAt: "2026-09-06T00:00:00Z",
    blocks: [{
      kind: "tool",
      toolCallId: `tool-${index}`,
      name: "web_search",
      args: JSON.stringify({ query: `query-${index}` }),
      result: `result-${index}`,
      status: "done",
    }],
  }));
}

/** Supply only browser layout/observer primitives that jsdom does not implement. */
class ResizeObserverHarness implements ResizeObserver {
  static instances = new Set<ResizeObserverHarness>();
  readonly targets = new Set<Element>();

  constructor(private readonly callback: ResizeObserverCallback) {
    ResizeObserverHarness.instances.add(this);
  }

  observe(target: Element) { this.targets.add(target); }
  unobserve(target: Element) { this.targets.delete(target); }
  disconnect() {
    this.targets.clear();
    ResizeObserverHarness.instances.delete(this);
  }

  static notify(target: HTMLElement) {
    const rect = target.getBoundingClientRect();
    const entry: ResizeObserverEntry = {
      target,
      contentRect: rect,
      borderBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      contentBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
      devicePixelContentBoxSize: [],
    };
    for (const observer of [...this.instances]) {
      if (observer.targets.has(target)) observer.callback([entry], observer);
    }
  }
}

describe("MessageTimeline disclosure scrolling", () => {
  let container: HTMLDivElement;
  let root: Root;
  let rowHeights: Map<string, number>;
  let scrollPositions: WeakMap<HTMLElement, number>;
  let frames: Map<number, FrameRequestCallback>;
  let scrollEvents: Set<HTMLElement>;
  let nextFrame: number;
  let originalProperties: Map<string, PropertyDescriptor | undefined>;
  let initialStore: ReturnType<typeof useConversationStore.getState>;

  const isViewport = (element: HTMLElement) =>
    element.dataset.slot === "scroll-area-viewport" && !element.closest("[data-index]");

  function rowHeight(element: HTMLElement) {
    if (element.parentElement?.matches("[data-quoteable-text]")) return 40;
    const id = element.querySelector<HTMLElement>("[data-message-id]")?.dataset.messageId;
    return id ? rowHeights.get(id) ?? ROW_HEIGHT : 0;
  }

  function contentHeight(element: HTMLElement) {
    // Radix places its display:table content wrapper between viewport and timeline.
    return Number.parseFloat(element.querySelector<HTMLElement>(":scope > div > div")?.style.height ?? "0") || 0;
  }

  function overrideProperty(name: string, descriptor: PropertyDescriptor) {
    originalProperties.set(name, Object.getOwnPropertyDescriptor(HTMLElement.prototype, name));
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, ...descriptor });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    initialStore = useConversationStore.getState();
    useConversationStore.setState({ runSnapshots: {}, runCalibrationPhase: "ready" });
    rowHeights = new Map();
    scrollPositions = new WeakMap();
    frames = new Map();
    scrollEvents = new Set();
    nextFrame = 1;
    originalProperties = new Map();
    ResizeObserverHarness.instances.clear();
    vi.stubGlobal("ResizeObserver", ResizeObserverHarness);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      const id = nextFrame++;
      frames.set(id, callback);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
    overrideProperty("offsetWidth", { get() { return 800; } });
    overrideProperty("clientWidth", { get() { return 800; } });
    overrideProperty("offsetHeight", {
      get(this: HTMLElement) { return isViewport(this) ? VIEWPORT_HEIGHT : rowHeight(this); },
    });
    overrideProperty("clientHeight", {
      get(this: HTMLElement) { return isViewport(this) ? VIEWPORT_HEIGHT : 0; },
    });
    overrideProperty("scrollHeight", {
      get(this: HTMLElement) { return isViewport(this) ? Math.max(this.clientHeight, contentHeight(this)) : this.clientHeight; },
    });
    overrideProperty("scrollTop", {
      get(this: HTMLElement) { return scrollPositions.get(this) ?? 0; },
      set(this: HTMLElement, value: number) {
        scrollPositions.set(this, Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)));
      },
    });
    overrideProperty("scrollTo", {
      value(this: HTMLElement, options: ScrollToOptions) {
        this.scrollTop = options.top ?? this.scrollTop;
        scrollEvents.add(this);
      },
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const height = this.offsetHeight;
      return { x: 0, y: 0, left: 0, top: 0, width: 800, height, right: 800, bottom: height, toJSON: () => ({}) };
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    useConversationStore.setState(initialStore, true);
    for (const [name, descriptor] of originalProperties) {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor);
      else Reflect.deleteProperty(HTMLElement.prototype, name);
    }
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  function viewport() {
    const element = container.querySelector<HTMLDivElement>('[data-slot="scroll-area-viewport"]');
    expect(element).not.toBeNull();
    return element!;
  }

  function row(messageId: string) {
    const element = container.querySelector(`[data-message-id="${messageId}"]`)?.closest<HTMLElement>("[data-index]");
    expect(element).toBeInstanceOf(HTMLElement);
    return element!;
  }

  function toolTrigger(messageId: string) {
    const element = row(messageId).querySelector<HTMLButtonElement>('button[aria-expanded]');
    expect(element).toBeInstanceOf(HTMLButtonElement);
    return element!;
  }

  async function flushFrames() {
    for (let iteration = 0; (frames.size || scrollEvents.size) && iteration < 20; iteration += 1) {
      const pending = [...frames.entries()];
      const pendingScrolls = [...scrollEvents];
      frames.clear();
      scrollEvents.clear();
      await act(async () => {
        for (const element of pendingScrolls) element.dispatchEvent(new Event("scroll"));
        for (const [, callback] of pending) callback(performance.now());
      });
    }
    expect(frames.size, "animation frames should settle").toBe(0);
  }

  async function renderTimeline(items: ChatMessage[] = messages(), streaming = false, overlayComposer = false,
    options: Partial<ComponentProps<typeof MessageTimeline>> = {}) {
    await act(async () => root.render(createElement(MessageTimeline, {
      messages: items, streaming, overlayComposer, ...options,
    })));
  }

  async function mountTimeline(items = messages(), overlayComposer = false) {
    await renderTimeline(items, false, overlayComposer);
    await measureMountedRows();
    await flushFrames();
    expectAtBottom();
    return items.at(-1)!.id;
  }

  async function measureMountedRows() {
    await act(async () => {
      for (const element of container.querySelectorAll<HTMLElement>("[data-index]")) {
        ResizeObserverHarness.notify(element);
      }
    });
  }

  async function resize(messageId: string, height: number) {
    rowHeights.set(messageId, height);
    await act(async () => ResizeObserverHarness.notify(row(messageId)));
  }

  async function clickTool(messageId: string) {
    await act(async () => toolTrigger(messageId).click());
  }

  async function scrollUp(distance = 100) {
    const element = viewport();
    await act(async () => {
      element.dispatchEvent(new WheelEvent("wheel", { deltaY: -distance, bubbles: true }));
      element.scrollTop -= distance;
      element.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
  }

  function expectAtBottom() {
    const element = viewport();
    expect(element.scrollHeight).toBeGreaterThan(element.clientHeight);
    expect(element.scrollTop).toBe(element.scrollHeight - element.clientHeight);
  }

  it("reveals a composer reference with the existing exact-range highlight and consumes it once", async () => {
    const items = messages();
    items[0] = { ...items[0], blocks: [{ kind: "text", mid: "source-block", text: "before quoted text after" }] };
    const reference: ChatTextReference = {
      referenceId: "draft-reference", quotedText: "quoted text", sourceRole: "assistant", availability: "available",
      source: { kind: "session_message", messageEventUuid: items[0].id, textBlockId: "source-block", externalSourceKey: null },
      locator: { kind: "text_range", coordinateSpace: "rendered_text_utf16_v1", start: 7, end: 18 },
    };
    const setHighlight = vi.fn();
    vi.stubGlobal("CSS", { highlights: { set: setHighlight, delete: vi.fn() } });
    vi.stubGlobal("Highlight", class { constructor(public range: Range) {} });
    const scrollBy = vi.fn();
    overrideProperty("scrollBy", { value: scrollBy });
    await mountTimeline(items);
    const handled = vi.fn();
    await renderTimeline(items, false, false, { referenceTarget: reference, onReferenceTargetHandled: handled });
    await flushFrames();
    expect(handled).toHaveBeenCalledOnce();
    expect(scrollBy).toHaveBeenCalled();
    expect(setHighlight).toHaveBeenCalledWith("agent-friend-quoted-text", expect.anything());
    expect(setHighlight.mock.calls[0][1].range.toString()).toBe("quoted text");
    await renderTimeline([...items], false, false, { referenceTarget: reference, onReferenceTargetHandled: handled });
    await flushFrames();
    expect(handled).toHaveBeenCalledOnce();
    expect(setHighlight).toHaveBeenCalledOnce();
    await renderTimeline(items);
    await renderTimeline(items, false, false, { referenceTarget: reference, onReferenceTargetHandled: handled });
    await flushFrames();
    expect(handled).toHaveBeenCalledTimes(2);
    expect(setHighlight).toHaveBeenCalledTimes(2);
  });

  it("loads one older page near the top and preserves the visible message through prepend", async () => {
    let finish!: (success: boolean) => void;
    const load = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    const options = { hasOlderHistory: true, onLoadOlderHistory: load };
    const items = messages();
    await renderTimeline(items, false, false, options);
    await measureMountedRows();
    await flushFrames();
    expect(load).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("加载更早的消息");
    await scrollUp(viewport().scrollTop - 100);
    expect(load).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("正在加载更早的消息");
    await scrollUp(10);
    expect(load).toHaveBeenCalledTimes(1);
    const anchorOffset = () => Number.parseFloat(row(items[0].id).style.transform.replace("translateY(", ""))
      - viewport().scrollTop;
    const before = anchorOffset();
    await renderTimeline([...messages("older"), ...items], false, false, options);
    await act(async () => finish(true));
    await measureMountedRows();
    await flushFrames();
    expect(anchorOffset()).toBe(before);
    expect(load).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("正在加载更早的消息");
    expect(container.querySelector('[aria-label="滚到底部"]')).not.toBeNull();
  });

  it.each(["false", "reject"])("stops automatic retries after %s and permits explicit retry", async (failure) => {
    const load = vi.fn<() => Promise<boolean>>();
    if (failure === "false") load.mockResolvedValueOnce(false);
    else load.mockRejectedValueOnce(new Error("offline"));
    load.mockResolvedValue(false);
    await renderTimeline(messages(), false, false, { hasOlderHistory: true, onLoadOlderHistory: load });
    await measureMountedRows();
    await flushFrames();
    await scrollUp(viewport().scrollTop);
    expect(load).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("更早的消息暂时未能加载");
    await scrollUp(20);
    expect(load).toHaveBeenCalledTimes(1);
    const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "重试");
    expect(retry).toBeDefined();
    await act(async () => retry!.click());
    expect(load).toHaveBeenCalledTimes(2);
  });

  it.each(["streaming", "historyLoading", "exhausted"])("does not fetch older history while %s", async (blocked) => {
    const load = vi.fn(async () => true);
    await renderTimeline(messages(), blocked === "streaming", false, {
      hasOlderHistory: blocked !== "exhausted", historyLoading: blocked === "historyLoading",
      onLoadOlderHistory: load,
    });
    await measureMountedRows();
    await flushFrames();
    await scrollUp(viewport().scrollTop);
    expect(load).not.toHaveBeenCalled();
  });

  it("does not load for automatic positioning or scrolling toward newer messages", async () => {
    const load = vi.fn(async () => true);
    await renderTimeline(messages(), false, false, { hasOlderHistory: true, onLoadOlderHistory: load });
    await measureMountedRows();
    await flushFrames();
    await act(async () => {
      viewport().scrollTop = 0;
      viewport().dispatchEvent(new Event("scroll"));
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaY: 50, bubbles: true }));
      viewport().scrollTop = 50;
      viewport().dispatchEvent(new Event("scroll"));
    });
    expect(load).not.toHaveBeenCalled();
  });

  it("loads a short page on upward input without a scroll event and shows the history end", async () => {
    const load = vi.fn(async () => true);
    const items = messages().slice(0, 1);
    await renderTimeline(items, false, false, { hasOlderHistory: true, onLoadOlderHistory: load });
    await measureMountedRows();
    await flushFrames();
    expect(load).not.toHaveBeenCalled();
    await act(async () => {
      viewport().dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    });
    expect(load).toHaveBeenCalledTimes(1);
    await renderTimeline(items, false, false, { hasOlderHistory: false, onLoadOlderHistory: load });
    expect(container.textContent).toContain("已到最早的消息");
    await scrollUp(20);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it.each(["pointerup", "pointercancel"])("keeps a held pointer scroll active across scrollend until %s", async (ending) => {
    await mountTimeline();
    const element = viewport();
    const pointer = (type: string) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperty(event, "pointerType", { value: "touch" });
      element.dispatchEvent(event);
    };
    await act(async () => {
      pointer("pointerdown");
      element.scrollTop -= 100;
      element.dispatchEvent(new Event("scroll"));
    });
    expect(container.querySelector('[aria-label="滚到底部"]')).not.toBeNull();
    await act(async () => {
      element.dispatchEvent(new Event("scrollend"));
      vi.advanceTimersByTime(300);
    });
    await act(async () => {
      element.scrollTop = element.scrollHeight;
      element.dispatchEvent(new Event("scroll"));
      pointer(ending);
      vi.advanceTimersByTime(300);
    });
    expectAtBottom();
    expect(container.querySelector('[aria-label="滚到底部"]')).toBeNull();
    // Once released, automatic movement must not be mistaken for a new gesture.
    await act(async () => {
      element.scrollTop -= 100;
      element.dispatchEvent(new Event("scroll"));
    });
    expect(container.querySelector('[aria-label="滚到底部"]')).toBeNull();
  });

  it("reveals live login actions and folds them again without reopening adjacent tools", async () => {
    const initialAuthStore = useSocialAuthRequestsStore.getState();
    const snapshot: SocialAuthRequestSnapshot = {
      requestId: "auth", sessionId: "session", platform: "bilibili", missingCapabilities: [],
      reason: "Login", originSurface: "chat", status: "pending", createdAt: "t", expiresAt: "t",
      continuationRunId: null, errorKind: null, challengeKind: "qr_image",
    };
    const updateAuth = (status?: SocialAuthRequestStatus) => act(async () => {
      useSocialAuthRequestsStore.setState({ entries: status ? {
        auth: { snapshot: { ...snapshot, status }, loading: false, submitting: false, error: null },
      } : {} });
    });
    const message: ChatMessage = {
      id: "login-message", role: "assistant", runId: "run", status: "complete", runOutcome: "completed",
      assistantPhase: { mode: "native", validation: "valid" }, createdAt: "2026-09-06T00:00:00Z",
      blocks: [
        { kind: "tool", toolCallId: "before", name: "calculate", args: "{}", status: "error" },
        { kind: "tool", toolCallId: "auth-tool", name: "request_social_auth", args: "{}", status: "done", result: '{"request_id":"auth","platform":"bilibili"}' },
        { kind: "tool", toolCallId: "after", name: "calculate", args: "{}", status: "done" },
        { kind: "text", mid: "answer", text: "Answer", phase: "final_answer" },
      ],
    };
    try {
      await updateAuth();
      await renderTimeline([message]);
      const visibleLoginButton = () => [...container.querySelectorAll("button")]
        .find((button) => button.textContent?.includes("开始登录") && !button.closest("[hidden]"));
      expect(visibleLoginButton()).toBeUndefined();
      await updateAuth("pending");
      expect(visibleLoginButton()).toBeDefined();
      expect(container.querySelector('button[aria-label="展开执行过程"]')).not.toBeNull();
      await updateAuth("login_in_progress");
      expect(container.querySelector('img[alt="Bilibili 登录二维码"]')?.closest("[hidden]")).toBeNull();
      for (const status of ["authenticated", "continuing", "completed", "cancelled", "expired"] as const) {
        await updateAuth(status);
        expect(visibleLoginButton()).toBeUndefined();
        expect([...container.querySelectorAll("button")].filter((button) => !button.closest("[hidden]") && button.getAttribute("aria-label") === "收起执行过程")).toHaveLength(0);
      }
      await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="展开执行过程"]')!.click());
      expect(container.textContent).toContain("已过期");
    } finally {
      await act(async () => useSocialAuthRequestsStore.setState(initialAuthStore, true));
    }
  });

  it.each([false, true])("keeps the bottom visible when a tool expands and collapses (overlayComposer=%s)", async (overlayComposer) => {
    const id = await mountTimeline(messages(), overlayComposer);
    const collapsedBottom = viewport().scrollTop;
    await clickTool(id);
    expect(toolTrigger(id).getAttribute("aria-expanded")).toBe("true");
    await resize(id, EXPANDED_ROW_HEIGHT);
    await flushFrames();
    expectAtBottom();
    expect(viewport().scrollTop).toBe(collapsedBottom + EXPANDED_ROW_HEIGHT - ROW_HEIGHT);

    await clickTool(id);
    expect(toolTrigger(id).getAttribute("aria-expanded")).toBe("false");
    await resize(id, ROW_HEIGHT);
    await flushFrames();
    expectAtBottom();
    expect(viewport().scrollTop).toBe(collapsedBottom);
  });

  it("holds the reading position when expanding after scrolling up", async () => {
    const id = await mountTimeline();
    await scrollUp();
    const readingPosition = viewport().scrollTop;
    await clickTool(id);
    await resize(id, EXPANDED_ROW_HEIGHT);
    await flushFrames();
    expect(viewport().scrollTop).toBe(readingPosition);
    expect(container.querySelector('[aria-label="滚到底部"]')).not.toBeNull();
  });

  it("commits row positions before a compensated resize callback returns", async () => {
    const items = messages();
    await mountTimeline(items);
    await scrollUp(100);
    await act(async () => { vi.advanceTimersByTime(200); });
    await flushFrames();
    const position = viewport().scrollTop;
    const readingRow = row(items[3].id);
    const rowStart = Number.parseFloat(readingRow.style.transform.replace("translateY(", ""));
    const growth = 80;
    rowHeights.set(items[0].id, ROW_HEIGHT + growth);

    await act(async () => {
      ResizeObserverHarness.notify(row(items[0].id));
      // Assert inside the observer delivery, before act/RAF can hide a stale-layout frame.
      expect(viewport().scrollTop).toBe(position + growth);
      const nextStart = Number.parseFloat(readingRow.style.transform.replace("translateY(", ""));
      expect(nextStart).toBe(rowStart + growth);
      expect(nextStart - viewport().scrollTop).toBe(rowStart - position);
    });
    await flushFrames();
  });

  it.each(["before measurement", "before animation frame"])(
    "lets an upward scroll take priority %s after disclosure",
    async (timing) => {
      const id = await mountTimeline();
      await clickTool(id);
      if (timing === "before animation frame") await resize(id, EXPANDED_ROW_HEIGHT);
      await scrollUp();
      const readingPosition = viewport().scrollTop;
      if (timing === "before measurement") await resize(id, EXPANDED_ROW_HEIGHT);
      await flushFrames();
      expect(viewport().scrollTop).toBe(readingPosition);
    },
  );

  it("honors an upward wheel intent before the browser applies its scroll offset", async () => {
    const id = await mountTimeline();
    await clickTool(id);
    const positionBeforeWheel = viewport().scrollTop;
    await act(async () => {
      viewport().dispatchEvent(new WheelEvent("wheel", { deltaY: -100, bubbles: true }));
    });
    await resize(id, EXPANDED_ROW_HEIGHT);
    await flushFrames();
    expect(viewport().scrollTop).toBe(positionBeforeWheel);
  });

  it("does not restore a stale disclosure hold over a later user scroll", async () => {
    const id = await mountTimeline();
    await scrollUp();
    await clickTool(id);
    await scrollUp(50);
    const readingPosition = viewport().scrollTop;
    await resize(id, EXPANDED_ROW_HEIGHT);
    await flushFrames();
    await act(async () => { vi.advanceTimersByTime(1_100); });
    expect(viewport().scrollTop).toBe(readingPosition);
  });

  it("preserves following through repeated measurement and streamed content growth", async () => {
    const items = messages();
    const id = await mountTimeline(items);
    await clickTool(id);
    for (const height of [300, 420, EXPANDED_ROW_HEIGHT]) {
      await resize(id, height);
      // Native layout/anchor scroll events are automatic, even when temporarily off the end.
      await act(async () => { viewport().dispatchEvent(new Event("scroll", { bubbles: true })); });
      await flushFrames();
      expectAtBottom();
    }
    await renderTimeline(items.map((message) => message.id === id
      ? { ...message, status: "streaming", blocks: [...message.blocks, { kind: "text", mid: "tail", text: "new output" }] }
      : message), true);
    await resize(id, 700);
    await flushFrames();
    expectAtBottom();
  });

  it.each(["disclosure hold", "queued follow"])("discards the old session's %s on session change", async (pending) => {
    const first = messages();
    await act(async () => {
      useConversationStore.setState({
        currentSessionId: "session-a", messages: first, historyLoading: false,
        historyLoadSeq: 0, syncRefreshSeq: 0, editingMessage: null, searchTarget: null,
      });
      root.render(createElement(MessageList, { primaryActivity: { surface: "none", kind: "none" } }));
    });
    await measureMountedRows();
    await flushFrames();
    if (pending === "disclosure hold") await scrollUp();
    await clickTool(first.at(-1)!.id);
    if (pending === "queued follow") await resize(first.at(-1)!.id, EXPANDED_ROW_HEIGHT);
    const oldViewport = viewport();

    await act(async () => useConversationStore.setState({ currentSessionId: "session-b", messages: messages("session-b") }));
    await measureMountedRows();
    await flushFrames();
    expect(viewport()).not.toBe(oldViewport);
    await scrollUp(70);
    const readingPosition = viewport().scrollTop;
    await act(async () => { vi.advanceTimersByTime(1_100); });
    await flushFrames();
    expect(viewport().scrollTop).toBe(readingPosition);
  });

  it("preserves upward reading when a cached history refresh finishes", async () => {
    await act(async () => {
      useConversationStore.setState({
        currentSessionId: "session-a", messages: messages(), historyLoading: false,
        historyLoadSeq: 0, syncRefreshSeq: 0, editingMessage: null, searchTarget: null,
      });
      root.render(createElement(MessageList, { primaryActivity: { surface: "none", kind: "none" } }));
    });
    await measureMountedRows();
    await flushFrames();
    expectAtBottom();
    await act(async () => useConversationStore.setState({ historyLoading: true }));
    await scrollUp(100);
    const readingViewport = viewport();
    const readingPosition = readingViewport.scrollTop;
    await act(async () => useConversationStore.setState({ historyLoading: false, historyLoadSeq: 1 }));
    await flushFrames();
    expect(viewport()).toBe(readingViewport);
    expect(viewport().scrollTop).toBe(readingPosition);
  });
});
