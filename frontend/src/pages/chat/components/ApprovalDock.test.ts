import { describe, expect, it } from "vitest";
import type { CommandApprovalRequest, CommandRuntimeStatus } from "@/services/api/commandRuntime";
import type { FilesystemApprovalEntry } from "@/stores/filesystemApprovals";
import { buildPendingApprovalQueue } from "./ApprovalDock";

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
      detail: expect.stringContaining("宿主用户（文件和网络不受限）"),
    });
  });
});
