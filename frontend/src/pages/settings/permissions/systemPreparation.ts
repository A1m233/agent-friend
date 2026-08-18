import type { PermissionItemSnapshot, PermissionFlowSnapshot } from "@/lib/permissions";
import type { SandboxCapabilitySnapshot } from "@/services/api/commandRuntime";

export interface SystemPreparationPlan {
  microphone: boolean;
  windowsSandbox: boolean;
  required: boolean;
  actions: Array<"microphone" | "windows_sandbox">;
}

export function systemPreparationPlan(
  microphone: PermissionItemSnapshot | undefined,
  activeFlow: PermissionFlowSnapshot | null,
  sandbox: SandboxCapabilitySnapshot | undefined,
): SystemPreparationPlan {
  const microphoneRequired = Boolean(
    activeFlow
    || (microphone
      && microphone.systemState !== "granted"
      && !microphone.deferredForCurrentRevision
      && microphone.recoveryAction !== "none"),
  );
  const windowsSandboxRequired = sandbox?.state === "setup_required"
    || sandbox?.state === "repair_required";
  return {
    microphone: microphoneRequired,
    windowsSandbox: windowsSandboxRequired,
    required: microphoneRequired || windowsSandboxRequired,
    actions: [
      ...(microphoneRequired ? ["microphone" as const] : []),
      ...(windowsSandboxRequired ? ["windows_sandbox" as const] : []),
    ],
  };
}
