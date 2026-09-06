// @vitest-environment jsdom
import { act, createElement, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ listen: vi.fn(), invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
import { applySettingCache, DEFAULT_SETTINGS, getSettingsSnapshot, useSetting } from "./index";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function Harness() {
  const [enabled] = useSetting("notificationSoundEnabled");
  return createElement("output", null, String(enabled));
}

let root: Root;
let container: HTMLDivElement;
let mounted: boolean;

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  window.__AGENT_FRIEND_SETTINGS__ = { ...DEFAULT_SETTINGS };
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mounted = true;
});

afterEach(async () => {
  if (mounted) await act(async () => { root.unmount(); });
  container.remove();
  delete window.__AGENT_FRIEND_SETTINGS__;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useSetting subscription readiness", () => {
  it("catches a page-cache change during StrictMode subscription setup and cleans both listeners", async () => {
    const oldSubscription = deferred<() => void>();
    const newSubscription = deferred<() => void>();
    const oldStop = vi.fn();
    const newStop = vi.fn();
    mocks.listen.mockReturnValueOnce(oldSubscription.promise).mockReturnValueOnce(newSubscription.promise);

    await act(async () => { root.render(createElement(StrictMode, null, createElement(Harness))); });
    expect(container.textContent).toBe("true");
    expect(mocks.listen).toHaveBeenCalledTimes(2);
    await act(async () => {
      applySettingCache("notificationSoundEnabled", false);
      newSubscription.resolve(newStop);
    });
    expect(container.textContent).toBe("false");

    await act(async () => { oldSubscription.resolve(oldStop); });
    expect(oldStop).toHaveBeenCalledOnce();
    expect(newStop).not.toHaveBeenCalled();
    expect(container.textContent).toBe("false");

    await act(async () => { root.unmount(); });
    mounted = false;
    expect(newStop).toHaveBeenCalledOnce();
  });

  it("keeps the current value and handles a failed subscription without an unhandled rejection", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    window.__AGENT_FRIEND_SETTINGS__ = { ...DEFAULT_SETTINGS, notificationSoundEnabled: false };
    mocks.listen.mockRejectedValue(new Error("subscription unavailable"));

    await act(async () => { root.render(createElement(Harness)); });
    await act(async () => { root.unmount(); });
    mounted = false;

    expect(warning).toHaveBeenCalledWith("setting subscription unavailable", expect.any(Error));
  });
});

describe("ActionBar saved page snapshot", () => {
  it.each([undefined, -1, 1.5, Number.NaN, 0x1_0000_0000])("defaults an absent or invalid page %s to zero", (page) => {
    window.__AGENT_FRIEND_SETTINGS__ = { petActionBarPage: page };
    expect(getSettingsSnapshot().petActionBarPage).toBe(0);
  });
  it.each([0, 2, 0xffff_ffff])("preserves valid page %s for layout-level clamping", (page) => {
    window.__AGENT_FRIEND_SETTINGS__ = { petActionBarPage: page };
    expect(getSettingsSnapshot().petActionBarPage).toBe(page);
  });
});
