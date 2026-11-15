// @vitest-environment jsdom
import * as React from "react";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { useActionBarPaging as usePaging } from "./useActionBarPaging";

const settings = vi.hoisted(() => ({ getSettingsSnapshot: vi.fn(), setSetting: vi.fn() }));
vi.mock("@/lib/settings", () => settings);
let useActionBarPaging: typeof usePaging;
let state: ReturnType<typeof usePaging>;
let root: Root;
let strip: HTMLDivElement;
let host: HTMLDivElement;
let vertical = false;
const bar = { current: null as HTMLDivElement | null };

function Probe() {
  state = useActionBarPaging(strip, vertical, 4, 17, bar);
  return null;
}
async function render() { await act(async () => root.render(createElement(Probe))); }
async function moved(offset: number) {
  strip[vertical ? "scrollTop" : "scrollLeft"] = offset;
  await act(async () => strip.dispatchEvent(new Event("scroll")));
}
beforeEach(async () => {
  vi.resetModules();
  vi.doMock("react", () => React);
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  ({useActionBarPaging} = await import("./useActionBarPaging"));
  settings.getSettingsSnapshot.mockReturnValue({ petActionBarPage: 0 });
  settings.setSetting.mockResolvedValue(undefined);
  host = document.createElement("div");
  strip = document.createElement("div");
  bar.current = document.createElement("div");
  bar.current.append(strip);
  for (const [key, value] of Object.entries({ clientHeight: 100, clientWidth: 100, scrollWidth: 430, scrollHeight: 430 })) {
    Object.defineProperty(strip, key, { value, configurable: true });
  }
  root = createRoot(host);
  vertical = false;
});
afterEach(async () => { await act(async () => root.unmount()); vi.unstubAllGlobals(); });

it.each([false, true])("moves by wheel distance along the visible axis (vertical=%s)", async (orientation) => {
  vertical = orientation;
  await render();
  for (const deltaY of [4, 3, 2]) {
    bar.current!.dispatchEvent(new WheelEvent("wheel", { deltaY, cancelable: true }));
  }
  expect(strip[vertical ? "scrollTop" : "scrollLeft"]).toBe(9);
  expect(settings.setSetting).not.toHaveBeenCalled();
  await moved(9);
  expect(state.canScrollPrev).toBe(true);
  await moved(330);
  expect(state.canScrollNext).toBe(false);
  expect(settings.setSetting).toHaveBeenLastCalledWith("petActionBarPage", 4);
});

it.each([false, true])("pages the native strip with a notched wheel (vertical=%s)", async (orientation) => {
  vertical = orientation;
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  strip.scrollTo = vi.fn();
  await render();
  await moved(32);
  const wheel = (deltaY: number, ctrlKey = false) =>
    bar.current!.dispatchEvent(new WheelEvent("wheel", { deltaY, deltaMode: 1, ctrlKey, cancelable: true }));
  expect(wheel(3, true)).toBe(true);
  expect(strip.scrollTo).not.toHaveBeenCalled();
  expect(wheel(3)).toBe(false);
  wheel(3);
  expect(strip.scrollTo).toHaveBeenCalledTimes(1);
  const axis = vertical ? "top" : "left";
  expect(strip.scrollTo).toHaveBeenCalledWith({ [axis]: 100, behavior: "smooth" });
  await moved(100);
  wheel(-3);
  expect(strip.scrollTo).toHaveBeenLastCalledWith({ [axis]: 0, behavior: "smooth" });
});

it("restores the saved page without writing it and keeps an exact offset across hover", async () => {
  settings.getSettingsSnapshot.mockReturnValue({ petActionBarPage: 2 });
  await render();
  expect(strip.scrollLeft).toBe(200);
  expect(settings.setSetting).not.toHaveBeenCalled();
  await moved(219);
  await act(async () => root.render(null));
  strip.scrollLeft = 0;
  await render();
  expect(strip.scrollLeft).toBe(219);
});

it("clamps an out-of-range preference without persisting the clamp", async () => {
  settings.getSettingsSnapshot.mockReturnValue({ petActionBarPage: 8 });
  await render();
  expect(strip.scrollLeft).toBe(330);
  expect(settings.setSetting).not.toHaveBeenCalled();
});

it("saves only the latest pending page after an in-flight write completes", async () => {
  let release!: () => void;
  settings.setSetting.mockReturnValueOnce(new Promise<void>((resolve) => { release = resolve; }));
  await render();
  await moved(100);
  await moved(200);
  await moved(300);
  expect(settings.setSetting.mock.calls).toEqual([["petActionBarPage", 1]]);
  await act(async () => release());
  expect(settings.setSetting.mock.calls).toEqual([["petActionBarPage", 1], ["petActionBarPage", 3]]);
});
