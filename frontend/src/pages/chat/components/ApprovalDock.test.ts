import { describe, expect, it } from "vitest";
import type {
  CommandApprovalRequest,
  CommandProcessSnapshot,
  CommandRuntimeStatus,
} from "@/services/api/commandRuntime";
import type { FilesystemApprovalEntry } from "@/stores/filesystemApprovals";
import { buildPendingApprovalQueue, buildRunningCommandQueue } from "./ApprovalDock";

function command(overrides: Partial<CommandApprovalRequest> = {}): CommandApprovalRequest {
  return {
    request_id: "cmd-1",
    kind: "command",
    session_id: "session-a",
    run_id: "run-a",
    tool_call_id: "tool-a",
    reason: "检查环境",
    requested_lifetime: "once",
    version: 1,
    status: "pending",
    details: {
      cmd: "maa version",
      argv: ["cmd.exe", "/c", "maa version"],
      execution: "sandboxed",
      workdir: "C:\\tools",
      cwd_source: "explicit",
      mode: "buffered",
      timeout_seconds: 30,
      filesystem_access: "read_only",
      additional_roots: [],
      host_derived_roots: [],
      network_access: "disabled",
      interactive: false,
      skill: null,
    },
    ...overrides,
  };
}

function filesystem(overrides: Partial<FilesystemApprovalEntry> = {}): FilesystemApprovalEntry {
  return {
    request: {
      request_id: "fs-1",
      session_id: "session-a",
      run_id: "run-a",
      tool_call_id: "tool-b",
      reason: "读取配置",
      items: [{
        item_id: "config",
        path: "C:\\config",
        root_kind: "directory",
        permissions: ["read_text"],
      }],
      actions: [],
      version: 1,
      status: "pending",
    },
    status: "pending",
    version: 1,
    recoverable: false,
    ...overrides,
  };
}

function commandProcess(
  overrides: Partial<CommandProcessSnapshot> = {},
): CommandProcessSnapshot {
  return {
    process_id: "process-1",
    session_id: "session-a",
    run_id: "run-a",
    tool_call_id: "tool-a",
    mode: "buffered",
    status: "running",
    started_at: "2026-08-21T00:00:00Z",
    duration_seconds: 3.5,
    output_seq: 0,
    dropped_bytes: 0,
    sandbox_denied: false,
    version: 1,
    stdout: "",
    stderr: "",
    pty: "",
    ...overrides,
  };
}

const runtimeReady: CommandRuntimeStatus = {
  available: true,
  platform: "windows",
  windows_sandbox_ready: true,
  requires_windows_sandbox_setup: false,
  sandbox_capability: {
    state: "ready",
    supported: true,
    ready: true,
    version: 1,
  },
};

describe("buildPendingApprovalQueue", () => {
  it("shows only pending approvals owned by the active run", () => {
    const queue = buildPendingApprovalQueue({
      currentSessionId: "session-a",
      activeRunIdBySession: { "session-a": "run-a" },
      commandApprovals: {
        current: command(),
        stale: command({ request_id: "cmd-old", run_id: "run-old" }),
        completed: command({ request_id: "cmd-done", status: "approved" }),
      },
      commandSubmitting: new Set(),
      commandRuntimeStatus: runtimeReady,
      filesystemEntries: { current: filesystem() },
    });

    expect(queue.map((item) => item.requestId)).toEqual(["cmd-1", "fs-1"]);
  });

  it("turns sandbox preparation into the single current command action", () => {
    const queue = buildPendingApprovalQueue({
      currentSessionId: "session-a",
      activeRunIdBySession: { "session-a": "run-a" },
      commandApprovals: { current: command() },
      commandSubmitting: new Set(),
      commandRuntimeStatus: {
        ...runtimeReady,
        windows_sandbox_ready: false,
        requires_windows_sandbox_setup: true,
        sandbox_capability: {
          ...runtimeReady.sandbox_capability,
          state: "setup_required",
          ready: false,
        },
      },
      filesystemEntries: {},
    });

    expect(queue[0]).toMatchObject({
      kind: "command",
      requestId: "cmd-1",
      needsSandboxSetup: true,
    });
  });

  it("keeps host-user approval actionable when the sandbox is not prepared", () => {
    const queue = buildPendingApprovalQueue({
      currentSessionId: "session-a",
      activeRunIdBySession: { "session-a": "run-a" },
      commandApprovals: {
        current: command({
          details: {
            ...command().details,
            execution: "host_user",
            filesystem_access: "unrestricted",
            network_access: "unrestricted",
          },
        }),
      },
      commandSubmitting: new Set(),
      commandRuntimeStatus: {
        ...runtimeReady,
        windows_sandbox_ready: false,
        requires_windows_sandbox_setup: true,
        sandbox_capability: {
          ...runtimeReady.sandbox_capability,
          state: "setup_required",
          ready: false,
        },
      },
      filesystemEntries: {},
    });

    expect(queue[0]).toMatchObject({
      needsSandboxSetup: false,
      command: "maa version",
      detailLines: expect.arrayContaining([
        "执行级别：宿主用户（文件和网络不受限）",
        "文件：不受限 · 网络：不受限",
      ]),
    });
  });

  it("keeps the complete command decision context in the dock model", () => {
    const queue = buildPendingApprovalQueue({
      currentSessionId: "session-a",
      activeRunIdBySession: { "session-a": "run-a" },
      commandApprovals: { current: command() },
      commandSubmitting: new Set(),
      commandRuntimeStatus: runtimeReady,
      filesystemEntries: {},
    });

    expect(queue[0]).toMatchObject({
      reason: "检查环境",
      command: "maa version",
      detailLines: [
        "工作目录：C:\\tools",
        "模式：普通 · 超时 30 秒",
        "执行级别：Codex 沙箱",
        "文件：只读 · 网络：禁止",
      ],
    });
  });
});

describe("buildRunningCommandQueue", () => {
  it("shows only running commands owned by the active run", () => {
    const queue = buildRunningCommandQueue({
      currentSessionId: "session-a",
      activeRunIdBySession: { "session-a": "run-a" },
      commandApprovals: { current: command({ status: "approved" }) },
      commandProcesses: {
        current: commandProcess(),
        terminal: commandProcess({ process_id: "process-done", status: "exited" }),
        stale: commandProcess({ process_id: "process-old", run_id: "run-old" }),
      },
    });

    expect(queue).toEqual([{
      processId: "process-1",
      reason: "检查环境",
      command: "maa version",
      detail: "普通 · 已运行 3.5 秒",
    }]);
  });
});
