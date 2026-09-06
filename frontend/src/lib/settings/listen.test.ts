import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  unlisten: vi.fn(),
  isTauri: vi.fn(() => true),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@/utils/tauri", () => ({ isTauri: mocks.isTauri }));

import {
  registerSettingsListener,
  syncDocumentTheme,
  syncDocumentThemeFromSettingsSnapshot,
  unregisterSettingsListener,
} from "./listen";
import { DEFAULT_SETTINGS, getSettingsSnapshot, type Settings } from "./index";

type SettingsListener = (event: { payload: { key: string; value: unknown } }) => void;
let changed: SettingsListener;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function createRoot() {
  const attrs = new Map<string, string>();
  const style = { colorScheme: "" };
  return {
    attrs,
    style,
    root: {
      style,
      setAttribute(name: string, value: string) {
        attrs.set(name, value);
      },
      removeAttribute(name: string) {
        attrs.delete(name);
      },
    },
  };
}

function setWindowSettings(settings: Partial<Settings>): void {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      __AGENT_FRIEND_SETTINGS__: {
        ...DEFAULT_SETTINGS,
        ...settings,
      },
    },
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.isTauri.mockReturnValue(true);
  mocks.listen.mockImplementation(async (_name: string, callback: SettingsListener) => {
    changed = callback;
    return mocks.unlisten;
  });
  mocks.invoke.mockResolvedValue({ ...DEFAULT_SETTINGS });
  setWindowSettings({});
});

afterEach(() => {
  unregisterSettingsListener();
  vi.restoreAllMocks();
  Reflect.deleteProperty(globalThis, "window");
});

describe("syncDocumentTheme", () => {
  it("sets project theme and TDesign theme-mode for dark theme", () => {
    const { attrs, root, style } = createRoot();

    syncDocumentTheme("dark", root);

    expect(attrs.get("theme")).toBe("dark");
    expect(attrs.get("theme-mode")).toBe("dark");
    expect(style.colorScheme).toBe("dark");
  });

  it("sets light theme and clears TDesign theme-mode", () => {
    const { attrs, root, style } = createRoot();
    attrs.set("theme-mode", "dark");

    syncDocumentTheme("light", root);

    expect(attrs.get("theme")).toBe("light");
    expect(attrs.has("theme-mode")).toBe(false);
    expect(style.colorScheme).toBe("light");
  });

  it("syncs the startup settings snapshot before React renders", () => {
    const { attrs, root } = createRoot();
    setWindowSettings({ theme: "dark" });

    syncDocumentThemeFromSettingsSnapshot(root);

    expect(attrs.get("theme")).toBe("dark");
    expect(attrs.get("theme-mode")).toBe("dark");
  });
});

describe("settings page bootstrap", () => {
  it("refreshes app-start values before the page is ready, including all notification settings", async () => {
    const current = {
      ...DEFAULT_SETTINGS,
      notificationCompletionMode: "never" as const,
      notificationNeedsActionEnabled: false,
      notificationFailureEnabled: false,
      notificationSoundEnabled: false,
      notificationSound: { kind: "default" as const },
      theme: "dark" as const,
    };
    const read = deferred<Settings>();
    mocks.invoke.mockReturnValue(read.promise);
    const ready = registerSettingsListener();
    const render = vi.fn(() => getSettingsSnapshot());
    const mounted = ready.then(render);

    expect(mocks.invoke).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(mocks.listen).toHaveBeenCalledWith("settings://changed", expect.any(Function));
    expect(mocks.invoke).toHaveBeenCalledWith("get_setting");
    expect(render).not.toHaveBeenCalled();
    read.resolve(current);
    await mounted;

    expect(render).toHaveReturnedWith(current);
    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
  });

  it("rereads when a setting event arrives during a stale snapshot response", async () => {
    const stale = deferred<Settings>();
    const current = deferred<Settings>();
    mocks.invoke.mockReturnValueOnce(stale.promise).mockReturnValueOnce(current.promise);
    const ready = registerSettingsListener();
    await Promise.resolve();

    changed({ payload: { key: "notificationSoundEnabled", value: false } });
    stale.resolve({ ...DEFAULT_SETTINGS });
    await Promise.resolve();
    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
    expect(mocks.invoke).toHaveBeenCalledTimes(2);
    current.resolve({ ...DEFAULT_SETTINGS, notificationSoundEnabled: false });
    await ready;
    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
  });

  it("keeps listening after bootstrap so another window's changes update the cache", async () => {
    await registerSettingsListener();
    changed({ payload: { key: "notificationSoundEnabled", value: false } });
    changed({ payload: { key: "notificationCompletionMode", value: "always" } });
    expect(getSettingsSnapshot()).toMatchObject({
      notificationSoundEnabled: false,
      notificationCompletionMode: "always",
    });
  });

  it("uses the cached state and resolves readiness when current settings cannot be read", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    setWindowSettings({ notificationSoundEnabled: false });
    mocks.invoke.mockRejectedValue(new Error("IPC unavailable"));

    await expect(registerSettingsListener()).resolves.toBeUndefined();

    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
    expect(warning).toHaveBeenCalledWith(
      "current settings unavailable; using cached bootstrap settings", expect.any(Error),
    );
  });

  it("still loads current settings if native event subscription fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    mocks.listen.mockRejectedValue(new Error("subscription unavailable"));
    mocks.invoke.mockResolvedValue({ ...DEFAULT_SETTINGS, notificationSoundEnabled: false });

    await registerSettingsListener();

    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
  });

  it("shares one page subscription and disposes it once before a new page registration", async () => {
    const first = registerSettingsListener();
    expect(registerSettingsListener()).toBe(first);
    await first;
    expect(mocks.listen).toHaveBeenCalledTimes(1);
    unregisterSettingsListener();
    unregisterSettingsListener();
    expect(mocks.unlisten).toHaveBeenCalledTimes(1);

    setWindowSettings({ notificationSoundEnabled: true });
    mocks.invoke.mockResolvedValue({ ...DEFAULT_SETTINGS, notificationSoundEnabled: false });
    await registerSettingsListener();
    expect(mocks.listen).toHaveBeenCalledTimes(2);
    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
  });

  it("disposes a subscription acquired after its page has been removed without fetching", async () => {
    const subscription = deferred<() => void>();
    mocks.listen.mockReturnValue(subscription.promise);
    const ready = registerSettingsListener();
    unregisterSettingsListener();
    subscription.resolve(mocks.unlisten);
    await ready;

    expect(mocks.unlisten).toHaveBeenCalledOnce();
    expect(mocks.invoke).not.toHaveBeenCalled();
  });

  it("ignores disposed page snapshots and event callbacks after a new page has loaded", async () => {
    const oldRead = deferred<Settings>();
    mocks.invoke.mockReturnValueOnce(oldRead.promise);
    const oldReady = registerSettingsListener();
    await Promise.resolve();
    const oldChanged = changed;
    unregisterSettingsListener();
    mocks.invoke.mockResolvedValue({ ...DEFAULT_SETTINGS, notificationSoundEnabled: false });
    await registerSettingsListener();

    oldRead.resolve({ ...DEFAULT_SETTINGS, notificationSoundEnabled: true });
    oldChanged({ payload: { key: "notificationSoundEnabled", value: true } });
    await oldReady;

    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
  });

  it("bounds retries under continuous changes without reverting the latest event", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    mocks.invoke.mockImplementation(async () => {
      changed({ payload: { key: "notificationSoundEnabled", value: false } });
      return { ...DEFAULT_SETTINGS };
    });

    await registerSettingsListener();

    expect(mocks.invoke).toHaveBeenCalledTimes(3);
    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
    expect(warning).toHaveBeenCalledWith(
      "settings changed during bootstrap; retaining the latest received cache",
    );
  });

  it("uses the seed without native calls for non-Tauri pages", async () => {
    mocks.isTauri.mockReturnValue(false);
    setWindowSettings({ notificationSoundEnabled: false });

    await registerSettingsListener();

    expect(getSettingsSnapshot().notificationSoundEnabled).toBe(false);
    expect(mocks.listen).not.toHaveBeenCalled();
    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});
