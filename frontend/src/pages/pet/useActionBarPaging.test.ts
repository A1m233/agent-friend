// @vitest-environment jsdom
import * as React from "react";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CarouselApi } from "@/components/ui";

const settings = vi.hoisted(() => ({ getSettingsSnapshot: vi.fn(), setSetting: vi.fn() }));
vi.mock("@/lib/settings", () => settings);

type EventName = "select" | "reInit";
type PagingState = ReturnType<typeof import("./useActionBarPaging").useActionBarPaging>;

function carousel(pageCount = 4, initialPage = 0) {
  let count = pageCount;
  let selected = initialPage;
  const listeners = new Map<EventName, Set<() => void>>();
  const emit = (event: EventName) => {
    for (const listener of [...(listeners.get(event) ?? [])]) listener();
  };
  const mock = {
    selectedScrollSnap: vi.fn(() => selected),
    canScrollPrev: vi.fn(() => selected > 0),
    canScrollNext: vi.fn(() => selected < count - 1),
    scrollSnapList: vi.fn(() => Array.from({ length: count }, (_, index) => index)),
    scrollTo: vi.fn((page: number, _jump?: boolean) => {
      const next = Math.max(0, Math.min(page, count - 1));
      if (next !== selected) { selected = next; emit("select"); }
    }),
    on: vi.fn((event: EventName, listener: () => void) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
      return api;
    }),
    off: vi.fn((event: EventName, listener: () => void) => {
      listeners.get(event)?.delete(listener);
      return api;
    }),
  };
  const api = mock as unknown as NonNullable<CarouselApi>;
  return {
    api, mock,
    userSelect(page: number) { mock.scrollTo(page); },
    rebuild(nextCount = count) { count = nextCount; selected = 0; },
    reInit(nextCount = count) { count = nextCount; selected = 0; emit("reInit"); },
    listenerCount(event: EventName) { return listeners.get(event)?.size ?? 0; },
  };
}

function deferred() {
  let resolve!: () => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("useActionBarPaging preference and writer", () => {
  let useActionBarPaging: typeof import("./useActionBarPaging").useActionBarPaging;
  let root: Root;
  let container: HTMLDivElement;
  let mounted: boolean;
  let persistedPage: number;
  let state: PagingState;

  function Probe({ api, enabled = true, layoutKey = "horizontal:4" }: {
    api: CarouselApi; enabled?: boolean; layoutKey?: string;
  }) {
    state = useActionBarPaging(api, enabled, layoutKey);
    return createElement("output", null, state.currentPage);
  }

  beforeEach(async () => {
    vi.resetModules();
    // Keep one real React dispatcher while resetting this hook's module preference/writer.
    vi.doMock("react", () => React);
    vi.resetAllMocks();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    persistedPage = 0;
    settings.getSettingsSnapshot.mockImplementation(() => ({ petActionBarPage: persistedPage }));
    settings.setSetting.mockImplementation(async (_key: string, page: number) => { persistedPage = page; });
    ({ useActionBarPaging } = await import("./useActionBarPaging"));
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    mounted = true;
  });

  afterEach(async () => {
    if (mounted) await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.doUnmock("react");
  });

  async function render(api: CarouselApi, enabled = true, layoutKey = "horizontal:4") {
    await act(async () => root.render(createElement(Probe, { api, enabled, layoutKey })));
  }

  async function hoverRemount(api: CarouselApi) {
    await act(async () => root.unmount());
    mounted = false;
    root = createRoot(container);
    mounted = true;
    await render(api);
  }

  it("restores a saved non-first page on initialization without writing zero", async () => {
    persistedPage = 2;
    const view = carousel();
    await render(view.api);
    expect(view.mock.scrollTo).toHaveBeenCalledWith(2, true);
    expect(state).toEqual({ startIndex: 2, currentPage: 2, canScrollPrev: true, canScrollNext: true });
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("saves a user's selection and restores it across hover unmount/remount", async () => {
    const first = carousel();
    await render(first.api);
    await act(async () => first.userSelect(2));
    expect(settings.setSetting).toHaveBeenCalledExactlyOnceWith("petActionBarPage", 2);
    expect(persistedPage).toBe(2);
    const next = carousel();
    await hoverRemount(next.api);
    expect(state.currentPage).toBe(2);
    expect(state.startIndex).toBe(2);
    expect(next.mock.scrollTo).toHaveBeenCalledWith(2, true);
    expect(settings.setSetting).toHaveBeenCalledTimes(1);
  });

  it("preserves an unsaved latest page across hover remount while the writer is in flight", async () => {
    const write = deferred();
    settings.setSetting.mockImplementation(async (_key: string, page: number) => {
      await write.promise; persistedPage = page;
    });
    const first = carousel();
    await render(first.api);
    await act(async () => first.userSelect(3));
    expect(persistedPage).toBe(0);
    const next = carousel();
    await hoverRemount(next.api);
    expect(state.currentPage).toBe(3);
    expect(state.startIndex).toBe(3);
    expect(settings.setSetting).toHaveBeenCalledExactlyOnceWith("petActionBarPage", 3);
    await act(async () => write.resolve());
    expect(persistedPage).toBe(3);
  });

  it("restores after reInit resets its engine to zero without saving the restoration", async () => {
    persistedPage = 2;
    const view = carousel();
    await render(view.api);
    await act(async () => view.reInit());
    expect(state.currentPage).toBe(2);
    expect(view.mock.scrollTo).toHaveBeenLastCalledWith(2, true);
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("restores after an orientation/layout-key change and replaces rather than duplicates listeners", async () => {
    persistedPage = 2;
    const view = carousel();
    await render(view.api);
    view.rebuild();
    await render(view.api, true, "vertical:4");
    expect(state.currentPage).toBe(2);
    expect(view.listenerCount("select")).toBe(1);
    expect(view.listenerCount("reInit")).toBe(1);
    expect(view.mock.off).toHaveBeenCalledTimes(2);
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("restores a replacement API and detaches selection from the old API", async () => {
    persistedPage = 2;
    const first = carousel();
    await render(first.api);
    const next = carousel();
    await render(next.api);
    expect(state.currentPage).toBe(2);
    expect(first.listenerCount("select")).toBe(0);
    expect(first.listenerCount("reInit")).toBe(0);
    await act(async () => first.userSelect(0));
    expect(state.currentPage).toBe(2);
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("clamps a smaller page count without replacing the preference and restores it when capacity returns", async () => {
    persistedPage = 4;
    const view = carousel(6);
    await render(view.api);
    await act(async () => view.reInit(2));
    expect(state).toEqual({ startIndex: 4, currentPage: 1, canScrollPrev: true, canScrollNext: false });
    expect(persistedPage).toBe(4);
    expect(settings.setSetting).not.toHaveBeenCalled();
    await act(async () => view.reInit(6));
    expect(state.currentPage).toBe(4);
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("does not save disabled pagination as the first page and restores when it is enabled again", async () => {
    persistedPage = 3;
    const view = carousel();
    await render(view.api);
    await render(undefined, false, "horizontal:1");
    expect(state.currentPage).toBe(0);
    expect(state.canScrollPrev).toBe(false);
    expect(state.canScrollNext).toBe(false);
    expect(view.listenerCount("select")).toBe(0);
    const next = carousel();
    await render(next.api, true, "vertical:4");
    expect(state.currentPage).toBe(3);
    expect(settings.setSetting).not.toHaveBeenCalled();
  });

  it("lets a real user selection of the first page replace a clamped preference", async () => {
    persistedPage = 4;
    const view = carousel(6);
    await render(view.api);
    await act(async () => view.reInit(2));
    await act(async () => view.userSelect(0));
    expect(settings.setSetting).toHaveBeenCalledExactlyOnceWith("petActionBarPage", 0);
    await act(async () => view.reInit(6));
    expect(state.currentPage).toBe(0);
    expect(persistedPage).toBe(0);
  });

  it("serializes native writes, coalesces pending changes, and commits the final quick selection last", async () => {
    const writes: Array<{ page: number; gate: ReturnType<typeof deferred> }> = [];
    let inFlight = 0;
    let maximumInFlight = 0;
    settings.setSetting.mockImplementation(async (_key: string, page: number) => {
      const gate = deferred();
      writes.push({ page, gate });
      inFlight += 1;
      maximumInFlight = Math.max(maximumInFlight, inFlight);
      try { await gate.promise; persistedPage = page; } finally { inFlight -= 1; }
    });
    const view = carousel(6);
    await render(view.api);
    await act(async () => { view.userSelect(1); view.userSelect(2); view.userSelect(3); });
    expect(writes.map((write) => write.page)).toEqual([1]);
    expect(state.currentPage).toBe(3);
    await act(async () => writes[0].gate.resolve());
    expect(writes.map((write) => write.page)).toEqual([1, 3]);
    await act(async () => view.userSelect(4));
    await act(async () => writes[1].gate.resolve());
    expect(writes.map((write) => write.page)).toEqual([1, 3, 4]);
    await act(async () => writes[2].gate.resolve());
    expect(persistedPage).toBe(4);
    expect(maximumInFlight).toBe(1);
    await act(async () => view.userSelect(0));
    expect(writes.map((write) => write.page)).toEqual([1, 3, 4, 0]);
    await act(async () => writes[3].gate.resolve());
    expect(persistedPage).toBe(0);
  });

  it("continues its writer after rejection and does not drop a later selection", async () => {
    const writes: Array<{ page: number; gate: ReturnType<typeof deferred> }> = [];
    settings.setSetting.mockImplementation(async (_key: string, page: number) => {
      const gate = deferred();
      writes.push({ page, gate });
      await gate.promise;
      persistedPage = page;
    });
    const view = carousel();
    await render(view.api);
    await act(async () => { view.userSelect(1); view.userSelect(2); view.userSelect(3); });
    await act(async () => writes[0].gate.reject(new Error("native save failed")));
    expect(writes.map((write) => write.page)).toEqual([1, 3]);
    expect(console.warn).toHaveBeenCalledWith("[pet] ActionBar page persistence failed", expect.any(Error));
    await act(async () => writes[1].gate.resolve());
    expect(persistedPage).toBe(3);
    await act(async () => view.userSelect(2));
    expect(writes.map((write) => write.page)).toEqual([1, 3, 2]);
    await act(async () => writes[2].gate.resolve());
    expect(persistedPage).toBe(2);
  });

  it("removes every listener at unmount and ignores later old-engine events", async () => {
    const view = carousel();
    await render(view.api);
    expect(view.listenerCount("select")).toBe(1);
    expect(view.listenerCount("reInit")).toBe(1);
    await act(async () => root.unmount());
    mounted = false;
    expect(view.listenerCount("select")).toBe(0);
    expect(view.listenerCount("reInit")).toBe(0);
    await act(async () => { view.userSelect(2); view.reInit(); });
    expect(settings.setSetting).not.toHaveBeenCalled();
  });
});
