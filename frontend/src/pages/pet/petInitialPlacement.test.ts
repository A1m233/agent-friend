import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tauriMocks = vi.hoisted(() => ({
  availableMonitors: vi.fn(),
  cursorPosition: vi.fn(),
  invoke: vi.fn(),
}));

vi.mock("@tauri-apps/api/window", () => ({
  availableMonitors: tauriMocks.availableMonitors,
  cursorPosition: tauriMocks.cursorPosition,
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: tauriMocks.invoke,
}));

import { loadPetInitialPlacementPoint } from "./petMonitorViewport";

const monitors = [
  {
    position: { x: -2560, y: 0 },
    size: { width: 2560, height: 1600 },
    scaleFactor: 1.75,
  },
  {
    position: { x: 0, y: 0 },
    size: { width: 2560, height: 1440 },
    scaleFactor: 1.25,
  },
];

describe("pet initial placement · persistence before cursor fallback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(globalThis, "isTauri", {
      configurable: true,
      value: true,
    });
    tauriMocks.availableMonitors.mockResolvedValue(monitors);
  });

  afterEach(() => {
    Reflect.deleteProperty(globalThis, "isTauri");
  });

  it("restores a persisted anchor without reading the cursor", async () => {
    tauriMocks.invoke.mockResolvedValue({
      anchor: {
        monitor: { x: 0, y: 0, width: 2560, height: 1440, scaleMilli: 1250 },
        relativeAnchor: { x: 0.75, y: 0.5 },
      },
    });
    tauriMocks.cursorPosition.mockRejectedValue(new Error("cursor unavailable"));

    await expect(
      loadPetInitialPlacementPoint({ x: 0, y: 0, w: 4096, h: 1280 }),
    ).resolves.toEqual({ x: 3584, y: 576 });
    expect(tauriMocks.cursorPosition).not.toHaveBeenCalled();
  });

  it("reads the cursor only when no persisted anchor can be restored", async () => {
    tauriMocks.invoke.mockResolvedValue({});
    tauriMocks.cursorPosition.mockResolvedValue({ x: -1200, y: 300 });

    await expect(
      loadPetInitialPlacementPoint({ x: 0, y: 0, w: 4096, h: 1280 }),
    ).resolves.toEqual({ x: 1024, y: 640 });
    expect(tauriMocks.cursorPosition).toHaveBeenCalledOnce();
  });

  it("surfaces a cursor failure when no persisted anchor exists", async () => {
    tauriMocks.invoke.mockResolvedValue({});
    tauriMocks.cursorPosition.mockRejectedValue(new Error("cursor unavailable"));

    await expect(
      loadPetInitialPlacementPoint({ x: 0, y: 0, w: 4096, h: 1280 }),
    ).rejects.toThrow("cursor unavailable");
  });
});
