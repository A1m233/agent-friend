import { describe, expect, it } from "vitest";
import type { PermissionItemSnapshot } from "@/lib/permissions";
import type { SandboxCapabilitySnapshot } from "@/services/api/commandRuntime";
import { systemPreparationPlan } from "./systemPreparation";

const microphone = (systemState: PermissionItemSnapshot["systemState"]): PermissionItemSnapshot => ({
  id: "microphone",
  definitionRevision: 1,
  systemState,
  presentationState: systemState === "granted" ? "granted" : "not_requested",
  recoveryAction: systemState === "granted" ? "none" : "request",
  handledForCurrentRevision: systemState === "granted",
  deferredForCurrentRevision: false,
  changedAtMs: null,
});

const sandbox = (state: SandboxCapabilitySnapshot["state"]): SandboxCapabilitySnapshot => ({
  state,
  supported: true,
  ready: state === "ready",
  last_error: null,
  version: 1,
});

describe("systemPreparationPlan", () => {
  it("collects microphone and Windows sandbox into one preparation plan", () => {
    expect(systemPreparationPlan(
      microphone("not_determined"),
      null,
      sandbox("repair_required"),
    )).toEqual({
      microphone: true,
      windowsSandbox: true,
      required: true,
      actions: ["microphone", "windows_sandbox"],
    });
  });

  it("skips capabilities that are already ready", () => {
    expect(systemPreparationPlan(microphone("granted"), null, sandbox("ready"))).toEqual({
      microphone: false,
      windowsSandbox: false,
      required: false,
      actions: [],
    });
  });

  it("does not immediately reopen a permission the user deferred", () => {
    const deferred = { ...microphone("not_determined"), deferredForCurrentRevision: true };
    expect(systemPreparationPlan(deferred, null, sandbox("ready")).required).toBe(false);
  });
});
