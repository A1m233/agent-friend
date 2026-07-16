import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";
import {
  autostartErrorMessage,
  getAutostartStatus,
  setAutostartEnabled,
} from "./autostart";

const mockInvoke = vi.mocked(invoke);

describe("autostart service", () => {
  beforeEach(() => mockInvoke.mockReset());

  it("reads the operating-system state through the Rust facade", async () => {
    mockInvoke.mockResolvedValue({ supported: true, enabled: true });
    await expect(getAutostartStatus()).resolves.toEqual({
      supported: true,
      enabled: true,
    });
    expect(mockInvoke).toHaveBeenCalledWith("get_autostart_status");
  });

  it("passes the requested state and returns the verified state", async () => {
    mockInvoke.mockResolvedValue({ supported: true, enabled: false });
    await expect(setAutostartEnabled(false)).resolves.toEqual({
      supported: true,
      enabled: false,
    });
    expect(mockInvoke).toHaveBeenCalledWith("set_autostart_enabled", {
      enabled: false,
    });
  });

  it("maps structured runtime failures to actionable copy", () => {
    expect(autostartErrorMessage("autostart_debug_disabled: no")).toContain(
      "正式安装版",
    );
    expect(autostartErrorMessage("autostart_state_mismatch: no")).toContain(
      "Windows 启动项",
    );
  });
});
