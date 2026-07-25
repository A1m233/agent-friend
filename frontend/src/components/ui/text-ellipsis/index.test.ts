import { afterEach, describe, expect, it, vi } from "vitest";

import { isTextOverflowing, observeTextOverflow } from "./overflow";

type ResizeCallback = ConstructorParameters<typeof ResizeObserver>[0];

class ResizeObserverStub {
  static latest: ResizeObserverStub | null = null;

  readonly callback: ResizeCallback;
  readonly observe = vi.fn();
  readonly disconnect = vi.fn();
  readonly unobserve = vi.fn();

  constructor(callback: ResizeCallback) {
    this.callback = callback;
    ResizeObserverStub.latest = this;
  }

  notify(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

describe("TextEllipsis overflow measurement", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    ResizeObserverStub.latest = null;
  });

  it("ignores a one-pixel rounding difference", () => {
    expect(isTextOverflowing({ scrollWidth: 101, clientWidth: 100 })).toBe(false);
    expect(isTextOverflowing({ scrollWidth: 102, clientWidth: 100 })).toBe(true);
  });

  it("remeasures when the observed element width changes", () => {
    globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
    const element = { scrollWidth: 140, clientWidth: 100 } as HTMLElement;
    const changes: boolean[] = [];

    const disconnect = observeTextOverflow(element, (value) => changes.push(value));
    expect(changes).toEqual([true]);

    Object.assign(element, { clientWidth: 160 });
    ResizeObserverStub.latest?.notify();
    expect(changes).toEqual([true, false]);

    disconnect();
    expect(ResizeObserverStub.latest?.disconnect).toHaveBeenCalledOnce();
  });
});
