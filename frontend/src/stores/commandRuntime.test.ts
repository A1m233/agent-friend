import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  commandRuntimeApi,
  type CommandApprovalRequest,
  type ApplicationApprovalRequest,
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
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
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

  it("retains desktop approval pushes without creating a managed process", () => {
    const request: ApplicationApprovalRequest = { ...approval(), kind: "desktop_application",
      details: { executable_path: "C:\\Apps\\Viewer.exe", arguments: [], working_directory: "C:\\Apps",
        lifecycle: "user_desktop", sandboxed: false } };
    useCommandRuntimeStore.getState().handlePush({ kind: "approval_changed", session_id: "session-a",
      seq: 1, source_kind: null, events: [], approval: request });
    expect(useCommandRuntimeStore.getState().approvals[request.request_id]).toEqual(request);
    useCommandRuntimeStore.getState().finishRun("run-a");
    expect(useCommandRuntimeStore.getState().processes).toEqual({});
    expect(commandRuntimeApi.cancel).not.toHaveBeenCalled();
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

describe("command approval decisions", () => {
  const pending = () => approval();
  const decided = (allow = true, version = 2): CommandApprovalRequest => ({
    ...approval(version),
    status: allow ? "approved" : "denied",
    decision_source: "user_once",
  });
  const push = (request: CommandApprovalRequest) => {
    useCommandRuntimeStore.getState().handlePush({
      kind: "approval_changed", session_id: request.session_id,
      seq: request.version, source_kind: null, events: [], approval: request,
    });
  };

  beforeEach(() => {
    vi.resetAllMocks();
    useCommandRuntimeStore.getState().clear();
    useCommandRuntimeStore.setState({ approvals: { "approval-a": pending() } });
    vi.mocked(commandRuntimeApi.pending).mockResolvedValue([pending()]);
    vi.mocked(commandRuntimeApi.processes).mockResolvedValue([]);
    vi.mocked(commandRuntimeApi.status).mockResolvedValue({
      available: true, platform: "windows", windows_sandbox_ready: true,
      requires_windows_sandbox_setup: false,
      sandbox_capability: { state: "ready", supported: true, ready: true, version: 1 },
    });
    vi.mocked(commandRuntimeApi.decide).mockResolvedValue(decided());
  });

  it.each([true, false])("applies the server decision without a push and prevents another submission (%s)", async (allow) => {
    vi.mocked(commandRuntimeApi.decide).mockResolvedValue(decided(allow));
    const result = await useCommandRuntimeStore.getState().decide("approval-a", allow);
    expect(result).toEqual({ status: "applied" });
    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toEqual(decided(allow));
    expect(useCommandRuntimeStore.getState().submitting.size).toBe(0);
    await expect(useCommandRuntimeStore.getState().decide("approval-a", allow))
      .resolves.toEqual({ status: "ignored" });
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(1);
  });

  it("blocks a second click while the first decision is in flight", async () => {
    const gate = deferred<CommandApprovalRequest>();
    vi.mocked(commandRuntimeApi.decide).mockReturnValue(gate.promise);
    const first = useCommandRuntimeStore.getState().decide("approval-a", true);
    expect(useCommandRuntimeStore.getState().submitting.has("approval-a")).toBe(true);
    await expect(useCommandRuntimeStore.getState().decide("approval-a", false))
      .resolves.toEqual({ status: "ignored" });
    gate.resolve(decided());
    await first;
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(1);
  });

  it("keeps a newer push when an older decision response arrives", async () => {
    const gate = deferred<CommandApprovalRequest>();
    vi.mocked(commandRuntimeApi.decide).mockReturnValue(gate.promise);
    const first = useCommandRuntimeStore.getState().decide("approval-a", true);
    const newer = { ...decided(true, 3), decision_source: "session_auto" as const };
    push(newer);
    gate.resolve(decided());
    await first;
    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toEqual(newer);
  });

  it("does not reopen an approved card from an older pending snapshot", async () => {
    const gate = deferred<CommandApprovalRequest[]>();
    vi.mocked(commandRuntimeApi.pending).mockReturnValueOnce(gate.promise);
    const refresh = useCommandRuntimeStore.getState().refresh();
    await useCommandRuntimeStore.getState().decide("approval-a", true);
    gate.resolve([pending()]);
    await refresh;
    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toEqual(decided());
  });

  it("does not restore a cleared store when the decision response arrives", async () => {
    const gate = deferred<CommandApprovalRequest>();
    vi.mocked(commandRuntimeApi.decide).mockReturnValue(gate.promise);
    const first = useCommandRuntimeStore.getState().decide("approval-a", true);
    useCommandRuntimeStore.getState().clear();
    gate.resolve(decided());
    await first;
    expect(useCommandRuntimeStore.getState().approvals).toEqual({});
    expect(useCommandRuntimeStore.getState().submitting.size).toBe(0);
  });

  it("reconciles a failed decision and returns its error for the UI before allowing a manual retry", async () => {
    vi.mocked(commandRuntimeApi.decide).mockRejectedValueOnce({ friendly: "服务暂时不可用", code: "network" });
    await expect(useCommandRuntimeStore.getState().decide("approval-a", true))
      .resolves.toEqual({ status: "failed", message: "服务暂时不可用" });
    expect(commandRuntimeApi.pending).toHaveBeenCalledTimes(1);
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(1);
    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status).toBe("pending");
    expect(useCommandRuntimeStore.getState().submitting.size).toBe(0);
    await useCommandRuntimeStore.getState().decide("approval-a", true);
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(2);
    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status).toBe("approved");
  });

  it("holds the submission guard until failure reconciliation finishes", async () => {
    const gate = deferred<CommandApprovalRequest[]>();
    vi.mocked(commandRuntimeApi.pending).mockReturnValue(gate.promise);
    vi.mocked(commandRuntimeApi.decide).mockRejectedValueOnce({ friendly: "连接断开", code: "network" });
    const first = useCommandRuntimeStore.getState().decide("approval-a", true);
    await vi.waitFor(() => expect(commandRuntimeApi.pending).toHaveBeenCalledTimes(1));
    await expect(useCommandRuntimeStore.getState().decide("approval-a", true))
      .resolves.toEqual({ status: "ignored" });
    gate.resolve([pending()]);
    await first;
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(1);
  });

  it("uses a confirmed push even if the HTTP response fails", async () => {
    const gate = deferred<CommandApprovalRequest>();
    vi.mocked(commandRuntimeApi.decide).mockReturnValue(gate.promise);
    vi.mocked(commandRuntimeApi.pending).mockResolvedValue([]);
    const first = useCommandRuntimeStore.getState().decide("approval-a", true);
    push(decided());
    gate.reject({ friendly: "连接断开", code: "network" });
    await expect(first).resolves.toEqual({ status: "applied" });
    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toEqual(decided());
  });

  it("retires a stale approval even when the refresh fails, and still accepts its later authoritative push", async () => {
    vi.mocked(commandRuntimeApi.decide).mockRejectedValueOnce({
      friendly: "这项授权已处理或已失效，请查看当前任务状态。", code: "approval_request_stale",
    });
    vi.mocked(commandRuntimeApi.pending).mockRejectedValueOnce(new Error("offline"));
    const result = await useCommandRuntimeStore.getState().decide("approval-a", true);
    expect(result.status).toBe("stale");
    await expect(useCommandRuntimeStore.getState().decide("approval-a", true))
      .resolves.toEqual({ status: "ignored" });
    expect(commandRuntimeApi.decide).toHaveBeenCalledTimes(1);
    push(decided());
    expect(useCommandRuntimeStore.getState().approvals["approval-a"]).toEqual(decided());
  });

  it("does not let an in-flight old snapshot reopen a stale approval", async () => {
    const gate = deferred<CommandApprovalRequest[]>();
    vi.mocked(commandRuntimeApi.pending).mockReturnValueOnce(gate.promise).mockResolvedValueOnce([]);
    const refresh = useCommandRuntimeStore.getState().refresh();
    vi.mocked(commandRuntimeApi.decide).mockRejectedValueOnce({ code: "approval_request_stale" });
    await useCommandRuntimeStore.getState().decide("approval-a", true);
    gate.resolve([pending()]);
    await refresh;
    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status).not.toBe("pending");
  });

  it.each([
    undefined,
    { ...decided(), request_id: "another-approval" },
    { ...decided(), run_id: "another-run" },
    { ...decided(), version: 1 },
    { ...decided(), status: "pending" },
  ])("does not claim approval from an invalid decision response (%j)", async (response) => {
    vi.mocked(commandRuntimeApi.decide).mockResolvedValue(response as CommandApprovalRequest);
    const result = await useCommandRuntimeStore.getState().decide("approval-a", true);
    expect(result.status).toBe("failed");
    expect(useCommandRuntimeStore.getState().approvals["approval-a"].status).toBe("pending");
  });
});
