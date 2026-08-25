import type {
  CommandProcessStatus,
  CommandRuntimeStatus,
} from "@/services/api/commandRuntime";

const SANDBOX_SETUP_ERRORS = new Set([
  "windows_sandbox_setup_required",
  "windows_sandbox_became_unavailable",
]);
const COMMAND_PROCESS_STATUSES = new Set<CommandProcessStatus>([
  "running",
  "exited",
  "cancelled",
  "timed_out",
  "runtime_lost",
]);

export interface CommandToolResultPresentation {
  error: string;
  message: string;
  status?: CommandProcessStatus;
  exitCode?: number | null;
  sandboxDenied?: boolean;
  terminalReason?: string | null;
  durationSeconds?: number;
  droppedBytes?: number;
  stdout: string;
  stderr: string;
  pty: string;
}

export function commandNeedsSandboxPreparation(
  resultError: string,
  runtimeStatus: CommandRuntimeStatus | null,
  execution: "sandboxed" | "host_user" = "sandboxed",
): boolean {
  if (execution === "host_user") return false;
  if (runtimeStatus) return runtimeStatus.requires_windows_sandbox_setup;
  return SANDBOX_SETUP_ERRORS.has(resultError);
}

export function commandExitedLabel(
  exitCode: number | null | undefined,
  sandboxDenied: boolean | undefined,
): string {
  if (sandboxDenied) return "沙箱拒绝";
  return `退出码 ${exitCode ?? "未知"}`;
}

export function parseCommandToolResult(result: string | undefined): CommandToolResultPresentation {
  let payload: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(result || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      payload = parsed as Record<string, unknown>;
    }
  } catch {
    // Malformed or legacy results keep the card usable through empty fallbacks.
  }
  const status = typeof payload.status === "string"
      && COMMAND_PROCESS_STATUSES.has(payload.status as CommandProcessStatus)
    ? payload.status as CommandProcessStatus
    : undefined;
  const exitCode = payload.exit_code === null || typeof payload.exit_code === "number"
    ? payload.exit_code
    : undefined;
  return {
    error: typeof payload.error === "string" ? payload.error : "",
    message: typeof payload.message === "string" ? payload.message : "",
    status,
    exitCode,
    sandboxDenied: typeof payload.sandbox_denied === "boolean"
      ? payload.sandbox_denied
      : undefined,
    terminalReason: payload.terminal_reason === null || typeof payload.terminal_reason === "string"
      ? payload.terminal_reason
      : undefined,
    durationSeconds: typeof payload.duration_seconds === "number"
      ? payload.duration_seconds
      : undefined,
    droppedBytes: typeof payload.dropped_bytes === "number" ? payload.dropped_bytes : undefined,
    stdout: typeof payload.stdout === "string" ? payload.stdout : "",
    stderr: typeof payload.stderr === "string" ? payload.stderr : "",
    pty: typeof payload.pty === "string" ? payload.pty : "",
  };
}
