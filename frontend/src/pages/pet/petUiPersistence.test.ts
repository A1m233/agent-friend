import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tauriMocks = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: tauriMocks.invoke }));

import {
  loadPetUiPersistence,
  savePetFacing,
  savePetMonitorAnchor,
} from "./petUiPersistence";

describe("petUiPersistence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(globalThis, "isTauri", {
      configurable: true,
      value: true,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(globalThis, "isTauri");
  });

  it("loads the shared pet UI snapshot", async () => {
    tauriMocks.invoke.mockResolvedValue({ facing: "left", anchor: null });
    await expect(loadPetUiPersistence()).resolves.toEqual({
      facing: "left",
      anchor: null,
    });
    expect(tauriMocks.invoke).toHaveBeenCalledWith("get_pet_ui_persistence");
  });

  it("writes facing and anchor through independent commands", async () => {
    tauriMocks.invoke.mockResolvedValue(undefined);
    const anchor = {
      monitor: { x: 0, y: 0, width: 100, height: 200 },
      relativeAnchor: { x: 0.25, y: 0.75 },
    };

    await savePetFacing("left");
    await savePetMonitorAnchor(anchor);

    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(1, "set_pet_facing", {
      payload: { facing: "left" },
    });
    expect(tauriMocks.invoke).toHaveBeenNthCalledWith(2, "set_pet_monitor_anchor", {
      payload: { anchor },
    });
  });
});
