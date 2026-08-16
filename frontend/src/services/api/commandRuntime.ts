import { http } from "@/services/http";

export type ApprovalStatus = "pending" | "approved" | "denied" | "expired" | "cancelled";
export type CommandProcessStatus = "running" | "exited" | "cancelled" | "timed_out" | "runtime_lost";

export interface CommandApprovalDetails {
  cmd: string;
  argv: string[];
  workdir: string;
  mode: "buffered" | "streaming";
  timeout_seconds: number;
  filesystem_access: "read_only" | "workspace_write";
  additional_roots: Array<{ path: string; access: "read_only" | "workspace_write" }>;
  network_access: "disabled" | "enabled";
  interactive: boolean;
  skill?: {
    skill_id: string;
    entry: string;
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
}

export interface CommandProcessSnapshot {
  process_id: string;
  session_id: string;
  run_id: string;
  tool_call_id: string;
  mode: "buffered" | "streaming";
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
  windows_sandbox_ready: boolean;
  requires_windows_sandbox_setup: boolean;
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
