import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  commandRuntimeApi,
  type CommandApprovalRequest,
  type CommandProcessSnapshot,
  type SandboxCapabilitySnapshot,
} from "@/services/api/commandRuntime";
import type { PushEnvelope } from "@/types/push";
import { preferNewerCommandRuntimeStatus, useCommandRuntimeStore } from "./commandRuntime";

vi.mock("@/services/api/commandRuntime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api/commandRuntime")>();
  return {
    ...actual,
    commandRuntimeApi: {
      pending: vi.fn(),
      processes: vi.fn(),
      status: vi.fn(),
      setupWindowsSandbox: vi.fn(),
      decide: vi.fn(),
      cancel: vi.fn(),
    },
  };
});

function approval(version = 1): CommandApprovalRequest {
  return {
    request_id: "approval-a",
    kind: "command",
    session_id: "session-a",
    run_id: "run-a",
    tool_call_id: "tool-a",
    reason: "运行测试",
    details: {
      cmd: "test",
      argv: ["test"],
      execution: "sandboxed",
      workdir: ".",
      cwd_source: "session_managed",
      mode: "buffered",
      timeout_seconds: 60,
      filesystem_access: "read_only",
      additional_roots: [],
      host_derived_roots: [],
      network_access: "disabled",
      interactive: false,
    },
    requested_lifetime: "once",
    version,
    status: "pending",
  };
}

function process(version = 1): CommandProcessSnapshot {
  return {
    process_id: "process-a",
    session_id: "session-a",
    run_id: "run-a",
    tool_call_id: "tool-a",
    mode: "buffered",
    status: "running",
    started_at: "2026-08-28T00:00:00Z",
    output_seq: 0,
    dropped_bytes: 0,
    sandbox_denied: false,
    version,
    stdout: "",
    stderr: "",
    pty: "",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

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
  beforeEach(() => {
    vi.clearAllMocks();
    useCommandRuntimeStore.getState().clear();
    vi.mocked(commandRuntimeApi.pending).mockResolvedValue([]);
    vi.mocked(commandRuntimeApi.processes).mockResolvedValue([]);
    vi.mocked(commandRuntimeApi.status).mockResolvedValue({
      available: true,
      platform: "windows",
      sandbox_capability: {
        state: "ready",
        supported: true,
        ready: true,
        version: 1,
      },
      windows_sandbox_ready: true,
      requires_windows_sandbox_setup: false,
    });
  });

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

  it("clears pending and running items missing from an authoritative refresh", async () => {
    vi.mocked(commandRuntimeApi.pending).mockResolvedValueOnce([approval()]);
    vi.mocked(commandRuntimeApi.processes).mockResolvedValueOnce([process()]);
    await useCommandRuntimeStore.getState().refresh();

    await useCommandRuntimeStore.getState().refresh();

    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status)
      .toBe("cancelled");
    expect(useCommandRuntimeStore.getState().processes["process-a"]).toMatchObject({
      status: "runtime_lost",
      terminal_reason: "missing_from_authority_snapshot",
    });
  });

  it("run terminal makes residual command items immediately non-actionable", () => {
    useCommandRuntimeStore.setState({
      approvals: { "approval-a": approval() },
      processes: { "process-a": process() },
    });

    useCommandRuntimeStore.getState().finishRun("run-a");

    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status)
      .toBe("cancelled");
    expect(useCommandRuntimeStore.getState().processes["process-a"]).toMatchObject({
      status: "runtime_lost",
      terminal_reason: "run_terminal",
    });
  });

  it("does not let an older collection response clear a newer approval push", async () => {
    const gate = deferred<CommandApprovalRequest[]>();
    vi.mocked(commandRuntimeApi.pending).mockReturnValueOnce(gate.promise);
    const refresh = useCommandRuntimeStore.getState().refresh();
    useCommandRuntimeStore.getState().handlePush({
      kind: "approval_changed",
      session_id: "session-a",
      seq: 2,
      source_kind: null,
      events: [],
      approval: approval(2),
    });
    gate.resolve([]);

    await refresh;

    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toMatchObject({
      status: "pending",
      version: 2,
    });
  });
});
