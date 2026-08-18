import { describe, expect, it } from "vitest";
import type { CommandRuntimeStatus } from "@/services/api/commandRuntime";
import { commandNeedsSandboxPreparation } from "./commandRuntimePresentation";

function status(ready: boolean): CommandRuntimeStatus {
  return {
    available: true,
    platform: "windows",
    sandbox_capability: {
      state: ready ? "ready" : "repair_required",
      supported: true,
      ready,
      last_error: null,
      version: 2,
    },
    windows_sandbox_ready: ready,
    requires_windows_sandbox_setup: !ready,
  };
}

describe("commandNeedsSandboxPreparation", () => {
  it("lets live ready state override an old setup error", () => {
    expect(commandNeedsSandboxPreparation("windows_sandbox_setup_required", status(true))).toBe(false);
  });

  it("uses the tool result only while live runtime state is unavailable", () => {
    expect(commandNeedsSandboxPreparation("windows_sandbox_became_unavailable", null)).toBe(true);
  });

  it("never routes an approved host-user command through sandbox preparation", () => {
    expect(commandNeedsSandboxPreparation("", status(false), "host_user")).toBe(false);
  });
});
