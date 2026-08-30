import { http } from "@/services/http";

export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "cancelled";
export type CommandProcessStatus = "running" | "exited" | "cancelled" | "timed_out" | "runtime_lost";
export type ApprovalDecisionSource =
  | "user_once"
  | "user_enable_session_auto"
  | "session_auto"
  | "catastrophic_policy";

export interface ApprovalRiskAssessment {
  level: "normal" | "catastrophic";
  fingerprint: string;
  code?: string | null;
  message?: string | null;
  evidence: string[];
}

export interface CommandApprovalDetails {
  cmd: string;
  argv: string[];
  execution: "sandboxed" | "host_user";
  workdir: string;
  cwd_source: "session_managed" | "explicit" | "skill_snapshot";
  mode: "buffered" | "streaming";
  timeout_seconds: number;
  filesystem_access: "read_only" | "read_write" | "unrestricted";
  additional_roots: Array<{ path: string; access: "read_only" | "read_write" }>;
  host_derived_roots: Array<{ path: string; access: "read_only" }>;
  network_access: "disabled" | "enabled" | "unrestricted";
  interactive: boolean;
  execution_environment?: {
    provider: string;
    runtime_version: string;
    identity: string;
    platform: string;
    architecture: string;
    status: "ready" | "degraded" | "unavailable";
    environment_revision: number;
    package_operation: boolean;
  };
  skill?: {
    skill_id: string;
    entry: string;
    workdir: string;
    fingerprint: string;
  } | null;
}

export interface CommandApprovalRequest {
  request_id: string;
  kind: "command";
  session_id: string;
  run_id: string;
  tool_call_id: string;
  reason: string;
  details: CommandApprovalDetails;
  requested_lifetime: "once";
  version: number;
  status: ApprovalStatus;
  approval_mode?: "per_request" | "session_auto";
  approval_mode_version?: number;
  decision_source?: ApprovalDecisionSource | null;
  risk?: ApprovalRiskAssessment | null;
}

export interface CommandProcessSnapshot {
  process_id: string;
  session_id: string;
  run_id: string;
  tool_call_id: string;
  mode: "buffered" | "streaming";
  recovery_mode?: "resident_only" | "queryable" | "reattachable";
  runtime_instance_id?: string;
  status: CommandProcessStatus;
  started_at: string;
  finished_at?: string | null;
  duration_seconds?: number | null;
  output_seq: number;
  dropped_bytes: number;
  exit_code?: number | null;
  sandbox_denied: boolean;
  terminal_reason?: string | null;
  sandbox_type?: string | null;
  version: number;
  stdout: string;
  stderr: string;
  pty: string;
}

export interface CommandRuntimeStatus {
  available: boolean;
  platform: "windows" | "other";
  sandbox_capability: SandboxCapabilitySnapshot;
  windows_sandbox_ready: boolean;
  requires_windows_sandbox_setup: boolean;
}

export type SandboxCapabilityState =
  | "unsupported"
  | "checking"
  | "setup_required"
  | "setting_up"
  | "ready"
  | "repair_required";

export interface SandboxCapabilitySnapshot {
  state: SandboxCapabilityState;
  supported: boolean;
  ready: boolean;
  last_error?: { error: string; message: string } | null;
  version: number;
}

export const commandRuntimeApi = {
  async pending(): Promise<CommandApprovalRequest[]> {
    const payload = await http.get("/v1/approvals?kind=command") as {
      requests: CommandApprovalRequest[];
    };
    return payload.requests;
  },

  async processes(): Promise<CommandProcessSnapshot[]> {
    const payload = await http.get("/v1/command-processes") as {
      processes: CommandProcessSnapshot[];
    };
    return payload.processes;
  },

  async status(): Promise<CommandRuntimeStatus> {
    return await http.get("/v1/command-runtime/status") as CommandRuntimeStatus;
  },

  async setupWindowsSandbox(): Promise<CommandRuntimeStatus> {
    return await http.post(
      "/v1/command-runtime/windows-sandbox/setup",
      {},
      { timeout: 310_000 },
    ) as CommandRuntimeStatus;
  },

  async decide(request: CommandApprovalRequest, allow: boolean): Promise<void> {
    await http.post(
      `/v1/approvals/${encodeURIComponent(request.request_id)}/command-decision`,
      {
        session_id: request.session_id,
        run_id: request.run_id,
        allow,
        lifetime: "once",
      },
    );
  },

  async cancel(process: CommandProcessSnapshot): Promise<void> {
    await http.post(
      `/v1/sessions/${encodeURIComponent(process.session_id)}`
      + `/runs/${encodeURIComponent(process.run_id)}`
      + `/commands/${encodeURIComponent(process.process_id)}/cancel`,
      {},
    );
  },
};
