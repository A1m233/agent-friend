import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const { invoke } = vi.hoisted(() => ({ invoke: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => true }));

describe("startup evidence", () => {
  beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); invoke.mockClear(); });
  afterEach(() => { vi.useRealTimers(); });
  it("waits for an enabled actual editor and every initialization milestone", async () => {
    const { recordStartupMilestone: mark, observeStartupComposer } = await import("./index");
    const editor = { isConnected: true, disabled: true, readOnly: false } as HTMLTextAreaElement;
    mark("settings-ready"); mark("root-committed"); mark("models-ready"); mark("restore-ready");
    const stop = observeStartupComposer(() => editor);
    vi.advanceTimersByTime(100);
    expect(invoke.mock.calls.some(([, value]) => value.milestone === "frontend-ready")).toBe(false);
    editor.disabled = false;
    vi.advanceTimersByTime(100);
    mark("root-committed");
    expect(invoke.mock.calls.filter(([, value]) => value.milestone === "frontend-ready")).toHaveLength(1);
    stop();
  });
  it("cleanup prevents a stale editor from completing a later restore", async () => {
    const { recordStartupMilestone: mark, observeStartupComposer } = await import("./index");
    mark("settings-ready"); mark("root-committed"); mark("models-ready");
    const stop = observeStartupComposer(() => ({ isConnected: true, disabled: false, readOnly: false }) as HTMLTextAreaElement);
    stop();
    mark("restore-ready");
    expect(invoke.mock.calls.some(([, value]) => value.milestone === "frontend-ready")).toBe(false);
  });
  it("unmounted or missing editors never report usable", async () => {
    const { observeStartupComposer } = await import("./index");
    const stop = observeStartupComposer(() => null);
    vi.advanceTimersByTime(120_100);
    expect(invoke).not.toHaveBeenCalled();
    stop();
  });
});
