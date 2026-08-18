import type { CommandRuntimeStatus } from "@/services/api/commandRuntime";

const SANDBOX_SETUP_ERRORS = new Set([
  "windows_sandbox_setup_required",
  "windows_sandbox_became_unavailable",
]);

export function commandNeedsSandboxPreparation(
  resultError: string,
  runtimeStatus: CommandRuntimeStatus | null,
  execution: "sandboxed" | "host_user" = "sandboxed",
): boolean {
  if (execution === "host_user") return false;
  if (runtimeStatus) return runtimeStatus.requires_windows_sandbox_setup;
  return SANDBOX_SETUP_ERRORS.has(resultError);
}
