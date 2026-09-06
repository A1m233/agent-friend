// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CommandApprovalRequest, CommandRuntimeStatus } from "@/services/api/commandRuntime";

const mocks = vi.hoisted(() => ({
  decide: vi.fn(), pending: vi.fn(), processes: vi.fn(), status: vi.fn(),
  cancel: vi.fn(), setupWindowsSandbox: vi.fn(),
  toastError: vi.fn(), toastInfo: vi.fn(), requestSessionAuto: vi.fn(),
}));

vi.mock("@/services/api/commandRuntime", () => ({ commandRuntimeApi: {
  decide: mocks.decide, pending: mocks.pending, processes: mocks.processes,
  status: mocks.status, cancel: mocks.cancel, setupWindowsSandbox: mocks.setupWindowsSandbox,
} }));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, info: mocks.toastInfo } }));
vi.mock("./SessionApprovalModeController", () => ({
  useSessionApprovalModeActions: () => ({
    requestSessionAutoFromApproval: mocks.requestSessionAuto,
    submittingRequestId: null,
  }),
}));

import { useCommandRuntimeStore } from "@/stores/commandRuntime";
import { ApprovalDock, buildPendingApprovalQueue } from "./ApprovalDock";

function approval(): CommandApprovalRequest {
  return {
    request_id: "approval-a", kind: "command", session_id: "session-a", run_id: "run-a",
    tool_call_id: "tool-a", reason: "检查测试夹具中的环境", requested_lifetime: "once",
    version: 1, status: "pending",
    details: {
      cmd: "fixture-command", argv: ["fixture-command"], execution: "sandboxed",
      workdir: ".", cwd_source: "session_managed", mode: "buffered", timeout_seconds: 30,
      filesystem_access: "read_only", additional_roots: [], host_derived_roots: [],
      network_access: "disabled", interactive: false,
    },
  };
}

const runtimeReady: CommandRuntimeStatus = {
  available: true, platform: "windows", windows_sandbox_ready: true,
  requires_windows_sandbox_setup: false,
  sandbox_capability: { state: "ready", supported: true, ready: true, version: 1 },
};

function approved(request: CommandApprovalRequest): CommandApprovalRequest {
  return { ...request, status: "approved", version: request.version + 1 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/** Exercise the production selector and store subscription without the full chat runtime. */
function ApprovalHarness() {
  const commandApprovals = useCommandRuntimeStore((state) => state.approvals);
  const commandSubmitting = useCommandRuntimeStore((state) => state.submitting);
  const commandRuntimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const queue = buildPendingApprovalQueue({
    currentSessionId: "session-a", activeRunId: "run-a", commandApprovals,
    commandSubmitting, commandRuntimeStatus, filesystemEntries: {},
  });
  return createElement(ApprovalDock, { activity: queue[0]
    ? { surface: "dock", kind: "approval", request: queue[0], queuedCount: queue.length - 1 }
    : { surface: "none", kind: "none" } });
}

describe("ApprovalDock command decision interaction", () => {
  let container: HTMLDivElement;
  let root: Root;
  let request: CommandApprovalRequest;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    useCommandRuntimeStore.getState().clear();
    request = approval();
    useCommandRuntimeStore.setState({
      approvals: { [request.request_id]: request }, runtimeStatus: runtimeReady,
    });
    mocks.pending.mockResolvedValue([]);
    mocks.processes.mockResolvedValue([]);
    mocks.status.mockResolvedValue(runtimeReady);
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    useCommandRuntimeStore.getState().clear();
    container.remove();
    vi.unstubAllGlobals();
  });

  async function mount() {
    await act(async () => root.render(createElement(ApprovalHarness)));
  }

  function button(label: string): HTMLButtonElement {
    const found = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((candidate) => candidate.textContent === label);
    expect(found).toBeDefined();
    return found!;
  }

  function expectApprovalRemoved() {
    expect(container.querySelector('[data-primary-activity="approval"]')).toBeNull();
    expect(container.textContent).not.toContain(request.reason);
    expect(container.textContent).not.toContain("仅本次允许");
  }

  it("removes the old card from the successful HTTP decision without waiting for a push", async () => {
    mocks.decide.mockResolvedValue(approved(request));
    await mount();
    expect(container.textContent).toContain(request.reason);
    const allow = button("仅本次允许");
    expect(allow.disabled).toBe(false);

    await act(async () => allow.click());

    expect(mocks.decide).toHaveBeenCalledExactlyOnceWith(request, true);
    expect(useCommandRuntimeStore.getState().approvals[request.request_id]).toMatchObject({
      status: "approved", version: 2,
    });
    expectApprovalRemoved();
    expect(allow.isConnected).toBe(false);
    expect(mocks.pending).not.toHaveBeenCalled();
    expect(mocks.toastError).not.toHaveBeenCalled();
    expect(mocks.toastInfo).not.toHaveBeenCalled();
  });

  it("disables the pending actions and submits only once during rapid repeated clicks", async () => {
    const pending = deferred<CommandApprovalRequest>();
    mocks.decide.mockReturnValue(pending.promise);
    await mount();
    const allow = button("仅本次允许");

    await act(async () => {
      allow.click();
      allow.click();
    });

    const processing = button("处理中…");
    expect(processing.disabled).toBe(true);
    expect(button("拒绝").disabled).toBe(true);
    expect(button("本会话无需授权").disabled).toBe(true);
    await act(async () => processing.click());
    expect(mocks.decide).toHaveBeenCalledExactlyOnceWith(request, true);
    expect(useCommandRuntimeStore.getState().submitting.has(request.request_id)).toBe(true);

    await act(async () => pending.resolve(approved(request)));

    expectApprovalRemoved();
    expect(useCommandRuntimeStore.getState().submitting.has(request.request_id)).toBe(false);
    expect(mocks.decide).toHaveBeenCalledTimes(1);
    expect(mocks.toastError).not.toHaveBeenCalled();
    expect(mocks.toastInfo).not.toHaveBeenCalled();
  });

  it("keeps the pending card retryable and shows the friendly error when reconciliation is still pending", async () => {
    const message = "本机服务暂时无法连接，请稍后重试。";
    mocks.decide.mockRejectedValue({ code: "network_error", friendly: message });
    mocks.pending.mockResolvedValue([request]);
    await mount();

    await act(async () => button("仅本次允许").click());

    expect(mocks.decide).toHaveBeenCalledExactlyOnceWith(request, true);
    expect(mocks.pending).toHaveBeenCalledTimes(1);
    expect(mocks.processes).toHaveBeenCalledTimes(1);
    expect(mocks.status).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain(request.reason);
    expect(button("仅本次允许").disabled).toBe(false);
    expect(button("拒绝").disabled).toBe(false);
    expect(useCommandRuntimeStore.getState().approvals[request.request_id].status).toBe("pending");
    expect(mocks.toastError).toHaveBeenCalledExactlyOnceWith("暂时无法确认授权结果", { description: message });
    expect(mocks.toastInfo).not.toHaveBeenCalled();
  });

  it("withdraws a stale card and explains that the approval is no longer actionable", async () => {
    mocks.decide.mockRejectedValue({ code: "approval_request_stale", friendly: "授权请求已失效" });
    mocks.pending.mockResolvedValue([]);
    await mount();

    await act(async () => button("仅本次允许").click());

    expect(mocks.decide).toHaveBeenCalledExactlyOnceWith(request, true);
    expect(mocks.pending).toHaveBeenCalledTimes(1);
    expectApprovalRemoved();
    expect(useCommandRuntimeStore.getState().approvals[request.request_id].status).toBe("cancelled");
    expect(mocks.toastInfo).toHaveBeenCalledExactlyOnceWith("这项授权已处理或已失效，请查看当前任务状态。");
    expect(mocks.toastError).not.toHaveBeenCalled();
  });
});
