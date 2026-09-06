// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { BubbleMeasurement, BubblePresentationState } from "@/pet/accessoryLayout/protocol";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(), record: vi.fn(), listen: vi.fn(), unlisten: vi.fn(),
  isTauri: vi.fn(() => true), visible: vi.fn(), dispose: vi.fn(), exit: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/utils/tauri", () => ({ isTauri: mocks.isTauri }));
vi.mock("@/stores/notifications", () => ({ recordNotificationPresented: mocks.record }));
vi.mock("./bubbleWindowVisibility", () => ({ acquireBubbleWindowVisibility: () => ({ setVisible: mocks.visible, dispose: mocks.dispose }) }));
import { BUBBLE_LAYOUT_APPLIED_EVENT } from "@/pet/accessoryLayout/protocol";
import { isNotificationVisible, useBubblePresentation } from "./useBubblePresentation";
const bubbleCss = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "PetBubble.css"), "utf8");

let stateSequence = 0, measurementRevision = 4;
function applied(changes: Partial<BubblePresentationState> = {}): BubblePresentationState {
  const stateRevision = changes.stateRevision ?? stateSequence + 1;
  stateSequence = Math.max(stateSequence, stateRevision);
  return { visible: true, applied: true, measurementRevision, layoutOwner: 1, layoutRevision: 7,
    widthCss: 360, heightCss: 400, scaleFactor: 1, retaining: true, carrierPlan: null,
    hostRectPhysical: { x: 100, y: 100, width: 360, height: 400 },
    targetRectPhysical: { x: 100, y: 100, width: 360, height: 400 }, ...changes, stateRevision };
}
function measured(revision = measurementRevision, retainBounds = true): BubbleMeasurement {
  return { revision, widthPhysical: 360, heightPhysical: 400, desiredWidthPhysical: 360,
    desiredHeightPhysical: 400, scaleFactor: 1, showRequested: true, retainBounds };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
let pendingCleanup: Array<() => void>;
function pendingMeasurement() {
  const pending = deferred<BubbleMeasurement>();
  pendingCleanup.push(() => pending.resolve(measured(999)));
  return pending;
}
type FakeAnimation = { node: HTMLElement; frames: Keyframe[]; finished: Promise<Animation>; finish(): void; cancel: ReturnType<typeof vi.fn>; done: boolean };
let animations: FakeAnimation[];
let root: Root, container: HTMLDivElement, viewport: HTMLDivElement, stack: HTMLDivElement;
let stylesheet: HTMLStyleElement;
let mounted = false, frameId = 0;
let frames: Map<number, FrameRequestCallback>;
let layoutListener: ((event: { payload: BubblePresentationState }) => void) | undefined;
let onMutation: (() => void) | undefined;
const disconnectResize = vi.fn(), disconnectMutation = vi.fn();
const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");

function cssRule(selector: string): CSSStyleDeclaration {
  const rule = [...stylesheet.sheet!.cssRules].find((candidate) => "selectorText" in candidate
    && (candidate as CSSStyleRule).selectorText.split(",").some((part) => part.trim() === selector)) as CSSStyleRule | undefined;
  expect(rule, `production CSS rule ${selector}`).toBeDefined();
  return rule!.style;
}

function item(id = "n1", { state = "active", text = "照片整理…", truncated = true,
  bounds = new DOMRect(16, 16, 240, 40), height = 80, sourceTitle = "", contentBounds = bounds }: {
    state?: string; text?: string; truncated?: boolean; bounds?: DOMRect;
    height?: number; sourceTitle?: string; contentBounds?: DOMRect;
  } = {}) {
  const outer = document.createElement("div");
  outer.dataset.bubbleItem = ""; outer.dataset.bubbleId = id; outer.dataset.state = state;
  outer.style.opacity = "1";
  let currentHeight = height;
  Object.defineProperty(outer, "offsetHeight", { get: () => Math.round(currentHeight) });
  outer.getBoundingClientRect = () => new DOMRect(16, Number.parseFloat(outer.style.top) || 16, 328, currentHeight);
  const content = document.createElement("span");
  content.dataset.notificationId = id; content.dataset.truncated = String(truncated); content.textContent = text;
  content.getBoundingClientRect = () => bounds;
  const contentGroup = document.createElement("div");
  contentGroup.dataset.notificationContent = "";
  contentGroup.getBoundingClientRect = () => contentBounds;
  if (sourceTitle) {
    const title = document.createElement("span");
    title.dataset.notificationSource = ""; title.textContent = sourceTitle; contentGroup.append(title);
  }
  contentGroup.append(content); outer.append(contentGroup); stack.append(outer);
  return { outer, content, contentGroup, setHeight: (value: number) => { currentHeight = value; } };
}
function Harness() { useBubblePresentation({ onExitComplete: mocks.exit }); return null; }
async function mount() {
  await act(async () => { root.render(createElement(Harness)); }); mounted = true;
}
async function flushFrame() {
  const callbacks = [...frames.values()]; frames.clear();
  await act(async () => { callbacks.forEach((callback) => callback(performance.now())); });
}
async function changed() { onMutation?.(); await flushFrame(); }
async function emitLayout(state = applied()) { await act(async () => { layoutListener?.({ payload: state }); }); }
async function finishAnimations() {
  await act(async () => { animations.filter((animation) => !animation.done).forEach((animation) => animation.finish()); });
}
const sizes = () => mocks.invoke.mock.calls.filter(([command]) => command === "set_bubble_size").map(([, args]) => args);
async function settleInitial() {
  await mount(); await flushFrame(); await emitLayout(); await finishAnimations();
  await emitLayout(applied({ retaining: false }));
  mocks.record.mockClear(); mocks.exit.mockClear(); mocks.visible.mockClear(); mocks.invoke.mockClear();
}

beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  stateSequence = 0; measurementRevision = 4; frameId = 0; frames = new Map(); animations = []; pendingCleanup = [];
  layoutListener = undefined; onMutation = undefined; mounted = false;
  mocks.record.mockResolvedValue(undefined); mocks.visible.mockResolvedValue(undefined); mocks.isTauri.mockReturnValue(true);
  mocks.invoke.mockImplementation(async (command: string, args?: { retainBounds?: boolean }) => command === "set_bubble_size"
    ? measured(++measurementRevision, args?.retainBounds) : applied({ visible: false, applied: false }));
  mocks.listen.mockImplementation(async (_name: string, listener: typeof layoutListener) => { layoutListener = listener; return mocks.unlisten; });
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect = disconnectResize; });
  vi.stubGlobal("MutationObserver", class { constructor(callback: () => void) { onMutation = callback; } observe() {} disconnect = disconnectMutation; });
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.stubGlobal("Animation", class {});
  Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: function (this: HTMLElement, keyframes: Keyframe[]) {
    let resolve!: (value: Animation) => void, reject!: (reason: unknown) => void;
    const finished = new Promise<Animation>((done, fail) => { resolve = done; reject = fail; });
    const animation: FakeAnimation = { node: this, frames: keyframes as Keyframe[], finished, done: false,
      finish() { this.done = true; resolve(this as unknown as Animation); },
      cancel: vi.fn(() => { animation.done = true; reject(new DOMException("cancelled", "AbortError")); }),
    };
    animations.push(animation); return animation as unknown as Animation;
  } });
  document.documentElement.style.setProperty("--motion-duration-notification", "200ms");
  document.documentElement.style.setProperty("--motion-ease-notification", "linear");
  stylesheet = document.createElement("style"); stylesheet.textContent = bubbleCss; document.head.append(stylesheet);
  viewport = document.createElement("div"); viewport.dataset.bubbleViewport = ""; viewport.dataset.transitioning = "false";
  viewport.getBoundingClientRect = () => new DOMRect(0, 0, 360, 400);
  stack = document.createElement("div"); stack.dataset.bubbleStack = ""; viewport.append(stack); document.body.append(viewport);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => { if (mounted) root.unmount(); pendingCleanup.forEach((finish) => finish()); });
  document.body.replaceChildren(); document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-bubble-window");
  stylesheet.remove();
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  if (originalAnimate) Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
  else Reflect.deleteProperty(HTMLElement.prototype, "animate");
});

describe("isNotificationVisible", () => {
  it.each([
    { label: "hidden native", state: { visible: false } }, { label: "unapplied native", state: { applied: false } },
    { label: "old measurement", state: { measurementRevision: 3 } },
  ])("rejects $label", ({ state }) => { expect(isNotificationVisible(item().content, applied(state), 4)).toBe(false); });
  it("requires a settled controlled item within both the native and scroll viewport", () => {
    const { outer, content } = item();
    expect(isNotificationVisible(content, applied(), 4)).toBe(true);
    viewport.dataset.transitioning = "true"; expect(isNotificationVisible(content, applied(), 4)).toBe(false);
    viewport.dataset.transitioning = "false"; outer.dataset.state = "exiting"; expect(isNotificationVisible(content, applied(), 4)).toBe(false);
    outer.dataset.state = "active"; outer.style.opacity = "0.5"; expect(isNotificationVisible(content, applied(), 4)).toBe(false);
    outer.style.opacity = "1"; viewport.getBoundingClientRect = () => new DOMRect(0, 0, 360, 50);
    expect(isNotificationVisible(content, applied(), 4)).toBe(false);
    viewport.getBoundingClientRect = () => new DOMRect(0, 0, 360, 400);
    expect(isNotificationVisible(content, applied({ heightCss: 50 }), 4)).toBe(false);
  });
  it("rejects notification-marked DOM outside the controlled stack", () => {
    const content = document.createElement("span"); document.body.append(content);
    expect(isNotificationVisible(content, applied(), 4)).toBe(false);
  });
  it("requires the source title and body together even when the body alone is visible", () => {
    const { content, contentGroup } = item("named", {
      sourceTitle: "整理书桌", bounds: new DOMRect(16, 24, 240, 40),
      contentBounds: new DOMRect(16, -8, 240, 72),
    });
    expect(content.getBoundingClientRect().top).toBeGreaterThanOrEqual(0);
    expect(isNotificationVisible(content, applied(), 4)).toBe(false);
    contentGroup.getBoundingClientRect = () => new DOMRect(16, 16, 240, 72);
    expect(isNotificationVisible(content, applied(), 4)).toBe(true);
  });
});

describe("single-window bubble presentation", () => {
  it("keeps document scrollbars disabled only for the marked bubble page and reserves its inner gutter", () => {
    const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../../bubble.html"), "utf8");
    const page = new DOMParser().parseFromString(html, "text/html");
    expect(page.documentElement.hasAttribute("data-bubble-window")).toBe(true);
    expect(cssRule("html[data-bubble-window]").getPropertyValue("overflow")).toBe("hidden");
    expect(cssRule("html[data-bubble-window] body").getPropertyValue("overflow")).toBe("hidden");
    expect(cssRule("[data-bubble-viewport]").getPropertyValue("scrollbar-gutter")).toBe("stable");
    expect(document.documentElement.matches("html[data-bubble-window]")).toBe(false);
    expect(document.body.matches("html[data-bubble-window] body")).toBe(false);
    document.documentElement.dataset.bubbleWindow = "";
    expect(document.body.matches("html[data-bubble-window] body")).toBe(true);
    expect(getComputedStyle(document.body).overflow).toBe("hidden");
  });

  it("acknowledges actual DOM text only after animation completion and the released native layout", async () => {
    item(); await mount(); await flushFrame();
    expect(mocks.listen).toHaveBeenCalledWith(BUBBLE_LAYOUT_APPLIED_EVENT, expect.any(Function));
    expect(sizes()).toEqual([{ width: 360, height: 112, retainBounds: true }]);
    await emitLayout(applied({ applied: false })); expect(animations).toHaveLength(0);
    await emitLayout(); expect(animations).toHaveLength(1);
    expect(mocks.record).not.toHaveBeenCalled();
    await act(async () => { document.dispatchEvent(new Event("scroll")); });
    expect(mocks.record).not.toHaveBeenCalled();
    await finishAnimations();
    expect(sizes().map((size) => size.retainBounds)).toEqual([true, false]);
    expect(mocks.record).not.toHaveBeenCalled();
    await emitLayout(applied({ retaining: false }));
    expect(mocks.record).toHaveBeenCalledExactlyOnceWith("n1", "照片整理…", true);
  });

  it("measures the full named row but acknowledges only its body after the title becomes visible", async () => {
    const { contentGroup } = item("named", {
      sourceTitle: "整理书桌", text: "那边有新回复了。", truncated: false, height: 104,
      bounds: new DOMRect(16, 24, 240, 40), contentBounds: new DOMRect(16, -8, 240, 72),
    });
    await mount(); await flushFrame();
    expect(sizes()).toEqual([{ width: 360, height: 136, retainBounds: true }]);
    await emitLayout(); await finishAnimations();
    await emitLayout(applied({ retaining: false }));
    expect(mocks.record).not.toHaveBeenCalled();
    contentGroup.getBoundingClientRect = () => new DOMRect(16, 16, 240, 72);
    await act(async () => { document.dispatchEvent(new Event("scroll")); });
    expect(mocks.record).toHaveBeenCalledExactlyOnceWith("named", "那边有新回复了。", false);
    expect(sizes().at(-1)).toEqual({ width: 360, height: 136, retainBounds: false });
  });

  it("keeps exiting DOM visible and retains bounds until every animation finishes", async () => {
    const first = item("first"); item("survivor"); await settleInitial();
    first.outer.dataset.state = "exiting"; item("new", { text: "新通知", truncated: false });
    await changed(); await emitLayout();
    const pending = animations.filter((animation) => !animation.done);
    expect(pending).toHaveLength(3); expect(mocks.visible).toHaveBeenCalledWith(true);
    expect(sizes().every((size) => size.retainBounds)).toBe(true);
    await act(async () => { pending[0].finish(); });
    expect(mocks.exit).not.toHaveBeenCalled(); expect(mocks.record).not.toHaveBeenCalled();
    expect(mocks.visible).not.toHaveBeenCalledWith(false);
    await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledExactlyOnceWith(["first"]);
    expect(sizes().map((size) => size.retainBounds)).toEqual([true, false]);
    expect(mocks.record).not.toHaveBeenCalled();
    await emitLayout(applied({ retaining: false }));
    expect(mocks.record.mock.calls.map(([id]) => id)).toEqual(["survivor", "new"]);
  });

  it("hides only after the final exit completes and the callback removes its DOM", async () => {
    const only = item(); await settleInitial();
    mocks.exit.mockImplementation((ids: string[]) => { if (ids.includes("n1")) { only.outer.remove(); onMutation?.(); } });
    only.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    expect(mocks.visible).not.toHaveBeenCalledWith(false);
    expect(mocks.exit).not.toHaveBeenCalled();
    await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledWith(["n1"]);
    expect(mocks.visible).not.toHaveBeenCalledWith(false);
    await flushFrame();
    expect(mocks.visible).toHaveBeenLastCalledWith(false);
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("retains the same CSS ratio through final native crop without a delayed JS offset reset", async () => {
    item(); await mount(); await flushFrame();
    await emitLayout(applied({ hostRectPhysical: { x: 100, y: 100, width: 360, height: 400 },
      targetRectPhysical: { x: 100, y: 388, width: 360, height: 112 } }));
    const retainedTop = viewport.style.top;
    expect(retainedTop).toContain("100vh - 112px");
    await finishAnimations();
    expect(viewport.style.top).toBe(retainedTop);
    expect(sizes().at(-1)?.retainBounds).toBe(false);
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("keeps fractional text height when sizing the surviving row after a close", async () => {
    const first = item("closing"), survivor = item("survivor", { height: 69.35 });
    expect(survivor.outer.offsetHeight).toBe(69);
    await settleInitial(); first.outer.dataset.state = "exiting";
    await changed();
    expect(sizes()).toEqual([{ width: 360, height: 102, retainBounds: true }]);
    await emitLayout();
    expect(stack.style.height).toBe("70px");
    await finishAnimations();
    expect(sizes().at(-1)).toEqual({ width: 360, height: 102, retainBounds: false });
  });

  it("lets FLIP rows cross the target viewport during animation and restores scrolling only when settled", async () => {
    const first = item("closing"); item("survivor"); await settleInitial();
    // JSDOM's shorthand/longhand computed cascade differs from WebView; assert
    // the live inline override and the actual CSS fallback independently.
    expect(viewport.style.overflow).toBe("");
    expect(cssRule("[data-bubble-viewport]").getPropertyValue("overflow-y")).toBe("auto");
    first.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    expect(viewport.style.overflow).toBe("visible");
    expect(mocks.exit).not.toHaveBeenCalled();
    await finishAnimations();
    expect(viewport.style.overflow).toBe("");
    expect(cssRule("[data-bubble-viewport]").getPropertyValue("overflow-y")).toBe("auto");
    expect(mocks.exit).toHaveBeenCalledWith(["closing"]);
  });

  it("preserves a fractional scrollbar gutter while animating and returns it to CSS at completion", async () => {
    viewport.getBoundingClientRect = () => new DOMRect(16, 16, 328, 400);
    stack.getBoundingClientRect = () => new DOMRect(16, 16, 312.8, 400);
    const first = item("closing"); item("survivor"); await settleInitial();
    expect(viewport.style.paddingRight).toBe("");
    mocks.exit.mockImplementation(() => {
      expect(viewport.style.overflow).toBe("");
      expect(viewport.style.paddingRight).toBe("");
    });
    first.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    expect(viewport.style.overflow).toBe("visible");
    expect(Number.parseFloat(viewport.style.paddingRight)).toBeCloseTo(15.2);
    expect(mocks.exit).not.toHaveBeenCalled();
    await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledWith(["closing"]);
    expect(viewport.style.paddingRight).toBe("");
    expect(cssRule("[data-bubble-viewport]").getPropertyValue("scrollbar-gutter")).toBe("stable");
  });

  it("installs fixed compensation for every row before committing native carrier growth", async () => {
    const first = item("present"), exiting = item("exiting", { state: "exiting" });
    const commit = deferred<BubblePresentationState>();
    pendingCleanup.push(() => commit.resolve(applied({ visible: false })));
    mocks.invoke.mockImplementation(async (command: string, args?: { retainBounds?: boolean }) => {
      if (command === "set_bubble_size") return measured(++measurementRevision, args?.retainBounds);
      if (command === "commit_bubble_carrier") {
        for (const node of [first.outer, exiting.outer]) {
          expect(node.style.position).toBe("fixed");
          expect(node.style.top).toContain("100vh - 200px");
          expect(node.style.width).toBe("328px");
        }
        return commit.promise;
      }
      return applied({ visible: false });
    });
    await mount(); await flushFrame();
    const union = { x: 100, y: -100, width: 360, height: 400 };
    const plan = { owner: 1, generation: 1, layoutRevision: 8, measurementRevision: 5, rectPhysical: union };
    const prepared = applied({ applied: false, layoutRevision: 8,
      hostRectPhysical: { x: 100, y: 100, width: 360, height: 200 }, targetRectPhysical: union, carrierPlan: plan });
    await emitLayout(prepared);
    expect(mocks.invoke).toHaveBeenCalledWith("commit_bubble_carrier", { plan });
    expect(animations).toHaveLength(0); expect(mocks.record).not.toHaveBeenCalled();
    await emitLayout(applied({ ...prepared, stateRevision: prepared.stateRevision + 1 }));
    expect(mocks.invoke.mock.calls.filter(([command]) => command === "commit_bubble_carrier")).toHaveLength(1);
    await act(async () => commit.resolve(applied({ layoutRevision: 8, hostRectPhysical: union, targetRectPhysical: union })));
    expect(animations).toHaveLength(2);
    expect(mocks.exit).not.toHaveBeenCalled();
    await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledWith(["exiting"]);
  });

  it("does not animate a late carrier commit after newer DOM has requested a different size", async () => {
    const node = item(); const commit = deferred<BubblePresentationState>();
    pendingCleanup.push(() => commit.resolve(applied({ visible: false })));
    mocks.invoke.mockImplementation(async (command: string, args?: { retainBounds?: boolean }) => {
      if (command === "set_bubble_size") return measured(++measurementRevision, args?.retainBounds);
      return command === "commit_bubble_carrier" ? commit.promise : applied({ visible: false });
    });
    await mount(); await flushFrame();
    const union = { x: 100, y: -100, width: 360, height: 600 };
    await emitLayout(applied({ applied: false, carrierPlan: { owner: 1, generation: 1, layoutRevision: 8,
      measurementRevision: 5, rectPhysical: union } }));
    node.setHeight(140); onMutation?.();
    await act(async () => commit.resolve(applied({ measurementRevision: 5, hostRectPhysical: union, targetRectPhysical: union })));
    expect(animations).toHaveLength(0);
    await flushFrame();
    expect(measurementRevision).toBe(6); expect(animations).toHaveLength(0);
    await emitLayout(applied({ measurementRevision: 6, hostRectPhysical: union, targetRectPhysical: union }));
    expect(animations).toHaveLength(1);
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("rejects a stale owner's carrier plan before committing it", async () => {
    item(); await mount(); await flushFrame();
    await emitLayout(applied({ applied: false, layoutOwner: 2, layoutRevision: 1 }));
    await emitLayout(applied({ applied: false, layoutOwner: 1, layoutRevision: 999,
      carrierPlan: { owner: 1, generation: 1, layoutRevision: 999, measurementRevision: 5,
        rectPhysical: { x: 100, y: -100, width: 360, height: 600 } } }));
    expect(mocks.invoke.mock.calls.filter(([command]) => command === "commit_bubble_carrier")).toHaveLength(0);
    expect(animations).toHaveLength(0);
  });

  it("cancels an obsolete close animation without releasing the newer transition early", async () => {
    const first = item("a"), second = item("b"); item("c"); await settleInitial();
    first.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    const old = animations.filter((animation) => !animation.done);
    second.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    expect(old.every((animation) => animation.cancel.mock.calls.length === 1)).toBe(true);
    expect(mocks.exit).not.toHaveBeenCalled(); expect(sizes().every((size) => size.retainBounds)).toBe(true);
    await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledExactlyOnceWith(["a", "b"]);
    expect(sizes().filter((size) => !size.retainBounds)).toHaveLength(1);
  });

  it("invalidates the old close completion immediately when another DOM change is scheduled", async () => {
    const first = item("a"), second = item("b"); item("c"); await settleInitial();
    first.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    second.outer.dataset.state = "exiting"; onMutation?.();
    await finishAnimations();
    expect(mocks.exit).not.toHaveBeenCalled();
    expect(sizes().every((size) => size.retainBounds)).toBe(true);
    await flushFrame(); await emitLayout(); await finishAnimations();
    expect(mocks.exit).toHaveBeenCalledWith(["a", "b"]);
  });

  it("does not acknowledge a new node between DOM mutation and its measurement frame", async () => {
    item("old"); await settleInitial(); item("new"); onMutation?.();
    await act(async () => { document.dispatchEvent(new Event("scroll")); });
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("does not let a scheduled same-layout mutation strand an animation completion", async () => {
    const first = item("a"); item("b"); await settleInitial();
    first.outer.dataset.state = "exiting"; await changed(); await emitLayout();
    onMutation?.();
    await finishAnimations();
    await flushFrame();
    expect(mocks.exit).toHaveBeenCalledExactlyOnceWith(["a"]);
    expect(sizes().filter((size) => !size.retainBounds)).toHaveLength(1);
  });

  it("completes without pending animations for reduced-motion users", async () => {
    vi.mocked(window.matchMedia).mockReturnValue({ matches: true } as MediaQueryList);
    const first = item(); await settleInitial(); first.outer.dataset.state = "exiting";
    await changed(); await emitLayout();
    expect(animations).toHaveLength(0); expect(mocks.exit).toHaveBeenCalledWith(["n1"]);
  });

  it("rejects old native snapshots and old owners while accepting a reset revision for the new owner", async () => {
    item(); await settleInitial();
    await emitLayout(applied({ visible: false, stateRevision: 20 }));
    await emitLayout(applied({ visible: true, stateRevision: 19 })); expect(mocks.record).not.toHaveBeenCalled();
    await emitLayout(applied({ layoutOwner: 2, layoutRevision: 1, stateRevision: 21 }));
    expect(mocks.record).toHaveBeenCalledTimes(1); mocks.record.mockClear();
    await emitLayout(applied({ layoutOwner: 1, layoutRevision: 999, visible: false, stateRevision: 22 }));
    await act(async () => { document.dispatchEvent(new Event("scroll")); });
    expect(mocks.record).toHaveBeenCalledTimes(1);
  });

  it("waits for a measurement response even if a newer native snapshot arrives first", async () => {
    const pending = pendingMeasurement();
    mocks.invoke.mockImplementation(async (command: string) => command === "set_bubble_size" ? pending.promise : applied({ visible: false }));
    item(); await mount(); await flushFrame(); await emitLayout(applied({ measurementRevision: 10 }));
    expect(animations).toHaveLength(0); expect(mocks.record).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(measured(10)); });
    expect(animations).toHaveLength(1); expect(mocks.record).not.toHaveBeenCalled();
  });

  it("does not animate an old size response between a DOM change and its next measurement frame", async () => {
    const pending = pendingMeasurement();
    mocks.invoke.mockImplementation(async (command: string) => command === "set_bubble_size" ? pending.promise : applied({ visible: false }));
    const node = item(); await mount(); await flushFrame();
    await emitLayout(applied({ measurementRevision: 5 }));
    node.setHeight(140); onMutation?.();
    await act(async () => { pending.resolve(measured(5)); });
    expect(animations).toHaveLength(0);
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("serializes native size changes, drops obsolete responses and coalesces unsent intermediate sizes", async () => {
    const first = pendingMeasurement(), fresh = pendingMeasurement(); let requests = 0;
    mocks.invoke.mockImplementation(async (command: string) => command === "set_bubble_size"
      ? ++requests === 1 ? first.promise : fresh.promise : applied({ visible: false }));
    const node = item(); await mount(); await flushFrame();
    node.setHeight(100); await changed(); node.setHeight(140); await changed();
    expect(requests).toBe(1);
    await act(async () => { first.resolve(measured(5)); });
    expect(sizes()).toEqual([{ width: 360, height: 112, retainBounds: true }, { width: 360, height: 172, retainBounds: true }]);
    await emitLayout(applied({ measurementRevision: 5 })); expect(animations).toHaveLength(0);
    await act(async () => { fresh.resolve(measured(6)); }); expect(animations).toHaveLength(0);
    await emitLayout(applied({ measurementRevision: 6 })); expect(animations).toHaveLength(1);
  });

  it("discards unsent measurements when all DOM nodes disappear", async () => {
    const first = pendingMeasurement();
    mocks.invoke.mockImplementation(async (command: string) => command === "set_bubble_size" ? first.promise : applied({ visible: false }));
    const node = item(); await mount(); await flushFrame(); node.setHeight(140); await changed();
    node.outer.remove(); await changed(); await act(async () => { first.resolve(measured(5)); });
    expect(sizes()).toHaveLength(1); expect(mocks.visible).toHaveBeenLastCalledWith(false);
    expect(animations).toHaveLength(0); expect(mocks.record).not.toHaveBeenCalled();
  });

  it("waits for the old effect's in-flight RPC and discards its queued work after remount", async () => {
    const old = pendingMeasurement(), fresh = pendingMeasurement(); let requests = 0;
    mocks.invoke.mockImplementation(async (command: string) => command === "set_bubble_size"
      ? ++requests === 1 ? old.promise : fresh.promise : applied({ visible: false }));
    const node = item(); await mount(); await flushFrame(); node.setHeight(120); await changed();
    await act(async () => { root.unmount(); }); mounted = false; root = createRoot(container);
    node.setHeight(160); await mount(); await flushFrame(); expect(requests).toBe(1);
    await act(async () => { old.resolve(measured(5)); });
    expect(sizes()).toEqual([{ width: 360, height: 112, retainBounds: true }, { width: 360, height: 192, retainBounds: true }]);
    await act(async () => { fresh.resolve(measured(6)); }); expect(mocks.record).not.toHaveBeenCalled();
    await emitLayout(applied({ measurementRevision: 6, layoutOwner: 2, layoutRevision: 1 }));
    expect(animations).toHaveLength(1); expect(mocks.dispose).toHaveBeenCalledTimes(1);
  });

  it("cleans observers, animations and ownership without releasing after unmount", async () => {
    item(); await mount(); await flushFrame(); await emitLayout();
    const pending = animations[0]; const count = sizes().length;
    await act(async () => { root.unmount(); }); mounted = false;
    await emitLayout(); await vi.advanceTimersByTimeAsync(2000);
    expect(pending.cancel).toHaveBeenCalledOnce(); expect(sizes()).toHaveLength(count);
    expect(mocks.record).not.toHaveBeenCalled(); expect(mocks.exit).not.toHaveBeenCalled();
    expect(mocks.dispose).toHaveBeenCalledOnce(); expect(mocks.unlisten).toHaveBeenCalledOnce();
    expect(disconnectResize).toHaveBeenCalledOnce(); expect(disconnectMutation).toHaveBeenCalledOnce();
  });

  it("does no native work outside Tauri", async () => {
    mocks.isTauri.mockReturnValue(false); item(); await mount(); await flushFrame();
    expect(mocks.invoke).not.toHaveBeenCalled(); expect(mocks.listen).not.toHaveBeenCalled(); expect(mocks.visible).not.toHaveBeenCalled();
  });
});
