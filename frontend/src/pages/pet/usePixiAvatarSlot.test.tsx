import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { createRoot } from "react-dom/client";
import { act as reactAct } from "react";
import { type ReactNode, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { usePixiAvatarSlot } from "./usePixiAvatarSlot";
import type { PetInteractions } from "./usePetInteractions";

// 轻量 PIXI mock：只保留 usePixiAvatarSlot 用到的 event / ticker / stage 行为
class MockTicker {
  private cb: ((t: number) => void) | null = null;
  add(cb: (t: number) => void): void {
    this.cb = cb;
  }
  remove(): void {
    this.cb = null;
  }
  tick(): void {
    this.cb?.(1);
  }
}

class MockContainer {
  label = "";
  eventMode = "static";
  cursor = "grab";
  x = 0;
  y = 0;
  hitArea: { x: number; y: number; width: number; height: number } | null = null;
  private children: unknown[] = [];
  private listeners: Record<string, ((e: PIXI.FederatedPointerEvent) => void)[]> = {};

  addChild(c: unknown): void {
    this.children.push(c);
  }

  on(event: string, cb: (e: PIXI.FederatedPointerEvent) => void): void {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(cb);
  }

  off(event: string): void {
    delete this.listeners[event];
  }

  emit(event: string, e: PIXI.FederatedPointerEvent): void {
    this.listeners[event]?.forEach((cb) => cb(e));
  }

  getBounds() {
    return {
      minX: this.x - 80,
      minY: this.y - 80,
      maxX: this.x + 80,
      maxY: this.y + 80,
      width: 160,
      height: 160,
    };
  }
}

class MockApp {
  stage = new MockContainer() as unknown as PIXI.Container;
  screen = { width: 800, height: 600 };
  canvas = document.createElement("canvas");
  ticker = new MockTicker();
  destroyed = false;

  async init(): Promise<void> {
    /* noop */
  }

  destroy(): void {
    this.destroyed = true;
  }
}

// 用 react-dom 实现的极简 renderHook
function renderHook<T, P>(useHook: (props: P) => T, initialProps: P): { result: { current: T }; rerender: (props: P) => void; unmount: () => void } {
  const result = { current: null as unknown as T };
  const container = document.createElement("div");
  document.body.appendChild(container);

  function Wrapper(props: { hookProps: P }): ReactNode {
    result.current = useHook(props.hookProps);
    return null;
  }

  const root = createRoot(container);
  const act = async (fn: () => void | Promise<void>) => {
    await reactAct(fn as () => void);
  };

  const rerender = (props: P) => {
    void act(() => root.render(<Wrapper hookProps={props} />));
  };

  rerender(initialProps);

  return {
    result,
    rerender,
    unmount: () => {
      void act(() => root.unmount());
      container.remove();
    },
  };
}

describe("usePixiAvatarSlot · 024 click/drag 区分", () => {
  let OriginalApplication: typeof PIXI.Application;
  let OriginalContainer: typeof PIXI.Container;
  let OriginalGraphics: typeof PIXI.Graphics;
  let OriginalText: typeof PIXI.Text;
  let OriginalRectangle: typeof PIXI.Rectangle;

  beforeAll(() => {
    OriginalApplication = PIXI.Application;
    OriginalContainer = PIXI.Container;
    OriginalGraphics = PIXI.Graphics;
    OriginalText = PIXI.Text;
    OriginalRectangle = PIXI.Rectangle;

    (PIXI as { Application: unknown }).Application = MockApp as unknown as typeof PIXI.Application;
    (PIXI as { Container: unknown }).Container = MockContainer as unknown as typeof PIXI.Container;
    (PIXI as { Graphics: unknown }).Graphics = vi.fn().mockImplementation(() => ({
      circle: vi.fn().mockReturnThis(),
      fill: vi.fn().mockReturnThis(),
    })) as unknown as typeof PIXI.Graphics;
    (PIXI as { Text: unknown }).Text = vi.fn().mockImplementation(() => ({
      anchor: { set: vi.fn() },
    })) as unknown as typeof PIXI.Text;
    (PIXI as { Rectangle: unknown }).Rectangle = vi.fn().mockImplementation((x, y, w, h) => ({ x, y, width: w, height: h })) as unknown as typeof PIXI.Rectangle;
  });

  afterAll(() => {
    (PIXI as { Application: unknown }).Application = OriginalApplication;
    (PIXI as { Container: unknown }).Container = OriginalContainer;
    (PIXI as { Graphics: unknown }).Graphics = OriginalGraphics;
    (PIXI as { Text: unknown }).Text = OriginalText;
    (PIXI as { Rectangle: unknown }).Rectangle = OriginalRectangle;
  });

  function makeEvent(x: number, y: number): PIXI.FederatedPointerEvent {
    return { global: { x, y } } as unknown as PIXI.FederatedPointerEvent;
  }

  async function waitForApp<T>(result: { current: T }) {
    const typed = result.current as unknown as { app: { ticker: MockTicker } | null };
    while (!typed.app) {
      await new Promise((r) => setTimeout(r, 10));
    }
  }

  it("短按短移触发 click，不触发 drag", async () => {
    const stage = document.createElement("div");
    const stageRef = { current: stage };
    const setSpriteScreen = vi.fn();
    const setIsDragging = vi.fn();
    const onSlotClick = vi.fn();

    const interactionsRef: RefObject<PetInteractions | null> = { current: { onSlotClick, onSlotDragMove: () => {} } };
    const { result, unmount } = renderHook(
      () => usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging, { interactionsRef }),
      undefined,
    );

    await waitForApp(result);
    const app = (result.current as unknown as { app: MockApp }).app;
    const slot = app.stage as unknown as MockContainer;

    await reactAct(() => {
      slot.emit("pointerdown", makeEvent(400, 300));
      slot.emit("pointerup", makeEvent(401, 302));
    });

    expect(onSlotClick).toHaveBeenCalledTimes(1);
    expect(setIsDragging).not.toHaveBeenCalled();
    unmount();
  });

  it("超阈值移动触发 drag，不触发 click", async () => {
    const stage = document.createElement("div");
    const stageRef = { current: stage };
    const setSpriteScreen = vi.fn();
    const setIsDragging = vi.fn();
    const onSlotClick = vi.fn();
    const onSlotDragMove = vi.fn();

    const interactionsRef: RefObject<PetInteractions | null> = { current: { onSlotClick, onSlotDragMove } };
    const { result, unmount } = renderHook(
      () => usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging, { interactionsRef }),
      undefined,
    );

    await waitForApp(result);
    const app = (result.current as unknown as { app: MockApp }).app;
    const slot = app.stage as unknown as MockContainer;

    await reactAct(() => {
      slot.emit("pointerdown", makeEvent(400, 300));
      slot.emit("globalpointermove", makeEvent(420, 320));
      slot.emit("pointerup", makeEvent(420, 320));
    });

    expect(setIsDragging).toHaveBeenCalledTimes(1);
    expect(onSlotClick).not.toHaveBeenCalled();
    expect(onSlotDragMove).toHaveBeenCalled();
    const [vx, _vy] = onSlotDragMove.mock.calls[0];
    expect(vx).toBeGreaterThan(0);
    expect(vx).toBeLessThanOrEqual(1);
    unmount();
  });

  it("长按但不移动不触发 click 也不触发 drag", async () => {
    const stage = document.createElement("div");
    const stageRef = { current: stage };
    const setSpriteScreen = vi.fn();
    const setIsDragging = vi.fn();
    const onSlotClick = vi.fn();

    const interactionsRef: RefObject<PetInteractions | null> = { current: { onSlotClick, onSlotDragMove: () => {} } };
    const { result, unmount } = renderHook(
      () => usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging, { interactionsRef }),
      undefined,
    );

    await waitForApp(result);
    const app = (result.current as unknown as { app: MockApp }).app;
    const slot = app.stage as unknown as MockContainer;

    let now = performance.now();
    const nowSpy = vi.spyOn(globalThis.performance, "now").mockImplementation(() => now);

    await reactAct(() => {
      slot.emit("pointerdown", makeEvent(400, 300));
      now += 400;
      slot.emit("pointerup", makeEvent(400, 300));
    });

    expect(onSlotClick).not.toHaveBeenCalled();
    expect(setIsDragging).not.toHaveBeenCalled();

    nowSpy.mockRestore();
    unmount();
  });
});
