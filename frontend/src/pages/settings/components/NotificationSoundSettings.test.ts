// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
import { NotificationSoundSettings } from "./NotificationSoundSettings";
import { DEFAULT_SETTINGS, getSettingsSnapshot, isNotificationSound, applySettingCache } from "@/lib/settings";

const custom = { kind: "custom" as const, assetId: `${"a".repeat(64)}.mp3`, name: "提醒.mp3" };
let root: Root;
let container: HTMLDivElement;
let mounted: boolean;

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  window.__AGENT_FRIEND_SETTINGS__ = { ...DEFAULT_SETTINGS, notificationSoundEnabled: false };
  mocks.listen.mockResolvedValue(() => undefined);
  mocks.invoke.mockResolvedValue(null);
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container); mounted = true;
});
afterEach(async () => {
  if (mounted) await act(async () => root.unmount());
  container.remove(); delete window.__AGENT_FRIEND_SETTINGS__;
  vi.unstubAllGlobals(); vi.useRealTimers();
});
async function render() { await act(async () => root.render(createElement(NotificationSoundSettings))); }
async function click(label: string) {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent === label);
  expect(button).toBeDefined();
  await act(async () => button!.click());
}

it("preserves a valid custom source through bootstrap and cross-window changes", async () => {
  window.__AGENT_FRIEND_SETTINGS__!.notificationSound = custom;
  expect(getSettingsSnapshot().notificationSound).toEqual(custom);
  await render();
  expect(container.textContent).toContain(custom.name);
  await act(async () => {
    applySettingCache("notificationSound", { kind: "default" });
    const [, callback] = mocks.listen.mock.calls.find(([event]) => event === "settings://changed")!;
    callback({ payload: { key: "notificationSound", value: { kind: "default" } } });
  });
  expect(container.textContent).not.toContain(custom.name);
  expect(isNotificationSound({ ...custom, assetId: "../audio.mp3" })).toBe(false);
  expect(isNotificationSound({ kind: "custom" })).toBe(false);
});

it("imports and resets using the authoritative returned source", async () => {
  await render();
  mocks.invoke.mockImplementation(async (command) => command === "import_notification_sound" ? custom : null);
  await click("选择音频");
  expect(getSettingsSnapshot().notificationSound).toEqual(custom);
  expect(container.textContent).toContain(custom.name);
  mocks.invoke.mockResolvedValue({ kind: "default" });
  await click("恢复默认");
  expect(getSettingsSnapshot().notificationSound).toEqual({ kind: "default" });
});

it("cancel and failed import retain the previous source and expose failure", async () => {
  window.__AGENT_FRIEND_SETTINGS__!.notificationSound = custom;
  await render(); await click("替换音频");
  expect(getSettingsSnapshot().notificationSound).toEqual(custom);
  mocks.invoke.mockRejectedValue("音频时长不能超过 30 秒");
  await click("替换音频");
  expect(getSettingsSnapshot().notificationSound).toEqual(custom);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("30 秒");
});

it("allows preview while muted without consuming a notification and stops on unmount", async () => {
  await render(); await click("试听");
  const call = mocks.invoke.mock.calls.find(([command]) => command === "preview_notification_sound")!;
  expect(call[1].previewId).toBeTruthy();
  expect(mocks.invoke.mock.calls.some(([command]) => command === "play_notification_sound")).toBe(false);
  await act(async () => root.unmount()); mounted = false;
  expect(mocks.invoke).toHaveBeenCalledWith("stop_notification_sound_preview", call[1]);
});

it("stops a preview that finishes starting after the page was closed", async () => {
  let resolve!: () => void;
  const pending = new Promise<void>((done) => { resolve = done; });
  mocks.invoke.mockImplementation((command) => command === "preview_notification_sound" ? pending : Promise.resolve(null));
  await render(); await click("试听");
  const args = mocks.invoke.mock.calls.find(([command]) => command === "preview_notification_sound")![1];
  await act(async () => root.unmount()); mounted = false;
  mocks.invoke.mockClear();
  await act(async () => resolve());
  expect(mocks.invoke).toHaveBeenCalledWith("stop_notification_sound_preview", args);
});

it("shows unavailable assets without changing the saved selection", async () => {
  window.__AGENT_FRIEND_SETTINGS__!.notificationSound = custom;
  mocks.invoke.mockResolvedValue("自定义提示音已丢失");
  await render();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("默认提示音");
  expect(getSettingsSnapshot().notificationSound).toEqual(custom);
});

it("returns to preview after playback completes and does not keep polling", async () => {
  vi.useFakeTimers();
  await render(); await click("试听");
  expect(container.textContent).toContain("停止试听");
  mocks.invoke.mockImplementation(async (command) => command === "notification_sound_preview_active" ? false : null);
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(container.textContent).not.toContain("停止试听");
  const count = mocks.invoke.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(mocks.invoke.mock.calls.length).toBe(count);
});

it("stops the previous preview when a native settings event selects another sound", async () => {
  await render(); await click("试听");
  const args = mocks.invoke.mock.calls.find(([command]) => command === "preview_notification_sound")![1];
  await act(async () => {
    applySettingCache("notificationSound", custom);
    const [, callback] = mocks.listen.mock.calls.find(([event]) => event === "settings://changed")!;
    callback({ payload: { key: "notificationSound", value: custom } });
  });
  expect(mocks.invoke).toHaveBeenCalledWith("stop_notification_sound_preview", args);
  expect(container.textContent).not.toContain("停止试听");
});
