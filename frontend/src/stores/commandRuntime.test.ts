import { beforeEach, describe, expect, it } from "vitest";
import type { SandboxCapabilitySnapshot } from "@/services/api/commandRuntime";
import type { PushEnvelope } from "@/types/push";
import { preferNewerCommandRuntimeStatus, useCommandRuntimeStore } from "./commandRuntime";

function capabilityEnvelope(
  state: "setup_required" | "setting_up" | "ready" | "repair_required",
  version: number,
): PushEnvelope {
  return {
    kind: "sandbox_capability_changed",
    session_id: "",
    seq: version,
    source_kind: null,
    events: [],
    sandbox_capability: {
      state,
      supported: true,
      ready: state === "ready",
      last_error: state === "repair_required"
        ? { error: "fixture", message: "需要修复" }
        : null,
      version,
    },
  };
}

describe("command runtime global sandbox capability", () => {
  beforeEach(() => useCommandRuntimeStore.getState().clear());

  it("updates the shared readiness state without approving a command", () => {
    useCommandRuntimeStore.getState().handlePush(capabilityEnvelope("setup_required", 1));
    expect(useCommandRuntimeStore.getState().runtimeStatus).toMatchObject({
      windows_sandbox_ready: false,
      requires_windows_sandbox_setup: true,
      sandbox_capability: { state: "setup_required" },
    });

    useCommandRuntimeStore.getState().handlePush(capabilityEnvelope("ready", 2));
    expect(useCommandRuntimeStore.getState().runtimeStatus).toMatchObject({
      windows_sandbox_ready: true,
      requires_windows_sandbox_setup: false,
      sandbox_capability: { state: "ready" },
    });
    expect(useCommandRuntimeStore.getState().approvals).toEqual({});
  });

  it("does not let an older capability event overwrite a newer state", () => {
    useCommandRuntimeStore.getState().handlePush(capabilityEnvelope("ready", 3));
    useCommandRuntimeStore.getState().handlePush(capabilityEnvelope("repair_required", 2));
    expect(useCommandRuntimeStore.getState().runtimeStatus?.sandbox_capability.state).toBe("ready");
  });

  it("does not let a stale status response overwrite a newer capability push", () => {
    const ready = capabilityEnvelope("ready", 3).sandbox_capability as SandboxCapabilitySnapshot;
    const staleRepair = capabilityEnvelope(
      "repair_required",
      2,
    ).sandbox_capability as SandboxCapabilitySnapshot;
    const merged = preferNewerCommandRuntimeStatus(
      {
        available: true,
        platform: "windows",
        sandbox_capability: ready,
        windows_sandbox_ready: true,
        requires_windows_sandbox_setup: false,
      },
      {
        available: true,
        platform: "windows",
        sandbox_capability: staleRepair,
        windows_sandbox_ready: false,
        requires_windows_sandbox_setup: true,
      },
    );
    expect(merged.sandbox_capability.state).toBe("ready");
    expect(merged.requires_windows_sandbox_setup).toBe(false);
  });
});
