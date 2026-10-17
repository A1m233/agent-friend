export interface StopDiagnosticFields {
  sessionId?: string | null;
  runId?: string | null;
  runtimeId?: string | null;
  phase?: string;
  reason?: string;
  result?: string;
  status?: string;
  durationMs?: number;
  disabled?: boolean;
}

/** Forwarded by the existing console bridge to tauri.log. Never log input or raw errors. */
export function logRunStop(
  event: "button_click" | "stop_event" | "decision" | "request_started" | "request_finished" | "terminal",
  fields: StopDiagnosticFields,
): void {
  try {
    const { sessionId, runId, runtimeId, phase, reason, result, status, durationMs, disabled } = fields;
    console.info("[run-stop]", {
      event, sessionId, runId, runtimeId, phase, reason, result, status, durationMs, disabled,
    });
  } catch { /* Diagnostics must never prevent cancellation. */ }
}

export function stopErrorCode(error: unknown): string {
  const code = error && typeof error === "object" && "code" in error ? error.code : null;
  switch (code) {
    case "run_runtime_mismatch":
    case "run_not_found":
    case "run_session_mismatch":
    case "run_not_desktop_controllable":
    case "network":
    case "http_error":
    case "invalid_response":
      return code;
    default: return "unknown";
  }
}
