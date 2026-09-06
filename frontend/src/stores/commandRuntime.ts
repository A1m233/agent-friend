import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { create } from "zustand";
import {
  commandRuntimeApi,
  type CommandApprovalRequest,
  type CommandProcessSnapshot,
  type CommandRuntimeStatus,
  type SandboxCapabilitySnapshot,
} from "@/services/api/commandRuntime";
import { friendlyErrorMessage } from "@/services/http";
import type { PushEnvelope } from "@/types/push";
import { isTauri } from "@/utils/tauri";
import {
  canReconcileSnapshotAbsence,
  emptySnapshotFence,
  touchSnapshotKey,
  type SnapshotFenceState,
} from "./snapshotFence";

type CommandApprovalResult =
  | { status: "applied" | "ignored" }
  | { status: "failed" | "stale"; message: string };

interface CommandRuntimeState {
  approvals: Record<string, CommandApprovalRequest>;
  processes: Record<string, CommandProcessSnapshot>;
  submitting: ReadonlySet<string>;
  runtimeStatus: CommandRuntimeStatus | null;
  approvalFence: SnapshotFenceState;
  processFence: SnapshotFenceState;
  settingUpSandbox: boolean;
  setupError: string | null;
  refresh: () => Promise<void>;
  handlePush: (envelope: PushEnvelope) => void;
  finishRun: (runId: string) => void;
  decide: (requestId: string, allow: boolean) => Promise<CommandApprovalResult>;
  cancel: (processId: string) => Promise<void>;
  setupWindowsSandbox: () => Promise<CommandRuntimeStatus | null>;
  markDisconnected: () => void;
  clear: () => void;
}

function validApproval(value: unknown): value is CommandApprovalRequest {
  const item = value as Partial<CommandApprovalRequest> | undefined;
  return item?.kind === "command"
    && typeof item.request_id === "string"
    && typeof item.session_id === "string"
    && typeof item.run_id === "string"
    && typeof item.tool_call_id === "string"
    && typeof item.version === "number"
    && typeof item.details?.cmd === "string";
}

function validProcess(value: unknown): value is CommandProcessSnapshot {
  const item = value as Partial<CommandProcessSnapshot> | undefined;
  return typeof item?.process_id === "string"
    && typeof item.session_id === "string"
    && typeof item.run_id === "string"
    && typeof item.tool_call_id === "string"
    && typeof item.status === "string"
    && typeof item.version === "number";
}

function validSandboxCapability(value: unknown): value is SandboxCapabilitySnapshot {
  const item = value as Partial<SandboxCapabilitySnapshot> | undefined;
  return typeof item?.state === "string"
    && typeof item.supported === "boolean"
    && typeof item.ready === "boolean"
    && typeof item.version === "number";
}

export function preferNewerCommandRuntimeStatus(
  current: CommandRuntimeStatus | null,
  incoming: CommandRuntimeStatus,
): CommandRuntimeStatus {
  if (!current) return incoming;
  if (current.sandbox_capability.version <= incoming.sandbox_capability.version) return incoming;
  const capability = current.sandbox_capability;
  return {
    ...incoming,
    sandbox_capability: capability,
    windows_sandbox_ready: capability.ready,
    requires_windows_sandbox_setup: capability.supported && !capability.ready,
  };
}

export const useCommandRuntimeStore = create<CommandRuntimeState>((set, get) => ({
  approvals: {},
  processes: {},
  submitting: new Set(),
  runtimeStatus: null,
  approvalFence: emptySnapshotFence(),
  processFence: emptySnapshotFence(),
  settingUpSandbox: false,
  setupError: null,
  async refresh() {
    const approvalRequestEpoch = get().approvalFence.mutationEpoch;
    const processRequestEpoch = get().processFence.mutationEpoch;
    const [approvals, processes, runtimeStatus] = await Promise.all([
      commandRuntimeApi.pending(),
      commandRuntimeApi.processes(),
      commandRuntimeApi.status(),
    ]);
    set((state) => {
      const nextApprovals = { ...state.approvals };
      let approvalFence = state.approvalFence;
      const approvalIds = new Set(approvals.map((item) => item.request_id));
      for (const item of approvals) {
        const current = nextApprovals[item.request_id];
        const touchedDuringRequest = !canReconcileSnapshotAbsence(
          state.approvalFence,
          item.request_id,
          approvalRequestEpoch,
        );
        if (!current || item.version > current.version || (!touchedDuringRequest && item.version === current.version)) {
          nextApprovals[item.request_id] = item;
        }
        approvalFence = touchSnapshotKey(approvalFence, item.request_id);
      }
      for (const [requestId, request] of Object.entries(nextApprovals)) {
        if (
          request.status !== "pending"
          || approvalIds.has(requestId)
          || !canReconcileSnapshotAbsence(state.approvalFence, requestId, approvalRequestEpoch)
        ) continue;
        nextApprovals[requestId] = { ...request, status: "cancelled" };
        approvalFence = touchSnapshotKey(approvalFence, requestId);
      }

      const nextProcesses = { ...state.processes };
      let processFence = state.processFence;
      const processIds = new Set(processes.map((item) => item.process_id));
      for (const item of processes) {
        const current = nextProcesses[item.process_id];
        const touchedDuringRequest = !canReconcileSnapshotAbsence(
          state.processFence,
          item.process_id,
          processRequestEpoch,
        );
        if (!current || item.version > current.version || (!touchedDuringRequest && item.version === current.version)) {
          nextProcesses[item.process_id] = item;
        }
        processFence = touchSnapshotKey(processFence, item.process_id);
      }
      for (const [processId, process] of Object.entries(nextProcesses)) {
        if (
          process.status !== "running"
          || processIds.has(processId)
          || !canReconcileSnapshotAbsence(state.processFence, processId, processRequestEpoch)
        ) continue;
        nextProcesses[processId] = {
          ...process,
          status: "runtime_lost",
          terminal_reason: "missing_from_authority_snapshot",
        };
        processFence = touchSnapshotKey(processFence, processId);
      }
      return {
        approvals: nextApprovals,
        processes: nextProcesses,
        approvalFence,
        processFence,
        runtimeStatus: preferNewerCommandRuntimeStatus(state.runtimeStatus, runtimeStatus),
      };
    });
  },
  handlePush(envelope) {
    if (envelope.kind === "approval_changed" && validApproval(envelope.approval)) {
      const request = envelope.approval;
      set((state) => {
        const current = state.approvals[request.request_id];
        if (current && request.version <= current.version) return {};
        return {
          approvals: { ...state.approvals, [request.request_id]: request },
          approvalFence: touchSnapshotKey(state.approvalFence, request.request_id),
        };
      });
    }
    if (envelope.kind === "command_process_changed" && validProcess(envelope.command_process)) {
      const process = envelope.command_process;
      set((state) => {
        const current = state.processes[process.process_id];
        if (current && process.version <= current.version) return {};
        return {
          processes: { ...state.processes, [process.process_id]: process },
          processFence: touchSnapshotKey(state.processFence, process.process_id),
        };
      });
    }
    if (
      envelope.kind === "sandbox_capability_changed"
      && validSandboxCapability(envelope.sandbox_capability)
    ) {
      const capability = envelope.sandbox_capability;
      set((state) => {
        const current = state.runtimeStatus?.sandbox_capability;
        if (current && current.version > capability.version) return {};
        return {
          runtimeStatus: state.runtimeStatus
            ? {
                ...state.runtimeStatus,
                sandbox_capability: capability,
                windows_sandbox_ready: capability.ready,
                requires_windows_sandbox_setup: !capability.ready,
              }
            : {
                available: capability.supported,
                platform: capability.supported ? "windows" : "other",
                sandbox_capability: capability,
                windows_sandbox_ready: capability.ready,
                requires_windows_sandbox_setup: capability.supported && !capability.ready,
              },
        };
      });
    }
  },
  finishRun(runId) {
    set((state) => {
      let approvalFence = state.approvalFence;
      let processFence = state.processFence;
      const approvals = Object.fromEntries(Object.entries(state.approvals).map(([key, request]) => {
        if (request.run_id !== runId || request.status !== "pending") return [key, request];
        approvalFence = touchSnapshotKey(approvalFence, key);
        return [key, { ...request, status: "cancelled" as const }];
      }));
      const processes = Object.fromEntries(Object.entries(state.processes).map(([key, process]) => {
        if (process.run_id !== runId || process.status !== "running") return [key, process];
        processFence = touchSnapshotKey(processFence, key);
        return [
          key,
          {
            ...process,
            status: "runtime_lost" as const,
            terminal_reason: "run_terminal",
          },
        ];
      }));
      return { approvals, processes, approvalFence, processFence };
    });
  },
  async decide(requestId, allow) {
    const request = get().approvals[requestId];
    if (!request || request.status !== "pending" || get().submitting.has(requestId)) {
      return { status: "ignored" };
    }
    set((state) => ({
      submitting: new Set(state.submitting).add(requestId),
      approvalFence: touchSnapshotKey(state.approvalFence, requestId),
    }));
    const decidedStatus = allow ? "approved" : "denied";
    try {
      const decided = await commandRuntimeApi.decide(request, allow);
      if (
        !validApproval(decided)
        || decided.request_id !== requestId
        || decided.session_id !== request.session_id
        || decided.run_id !== request.run_id
        || decided.version <= request.version
        || decided.status !== decidedStatus
      ) {
        throw new Error("授权已提交，但返回的状态无法确认。请查看当前任务状态。");
      }
      set((state) => {
        const current = state.approvals[requestId];
        // A cleared store or a newer push must not be replaced by a late HTTP response.
        if (!current || current.version >= decided.version) return {};
        return {
          approvals: { ...state.approvals, [requestId]: decided },
          approvalFence: touchSnapshotKey(state.approvalFence, requestId),
        };
      });
      return { status: "applied" };
    } catch (error) {
      const stale = typeof error === "object" && error !== null
        && "code" in error && error.code === "approval_request_stale";
      if (stale) {
        set((state) => {
          const current = state.approvals[requestId];
          if (!current || current.version > request.version || current.status !== "pending") return {};
          // Match the existing missing-from-authority reconciliation. Keep the version
          // so a later authoritative terminal push can still replace this local state.
          return {
            approvals: { ...state.approvals, [requestId]: { ...current, status: "cancelled" } },
            approvalFence: touchSnapshotKey(state.approvalFence, requestId),
          };
        });
      }
      // A failed response can still have reached the server. Reconcile before
      // enabling a retry, without automatically submitting the decision again.
      await get().refresh().catch(() => undefined);
      const current = get().approvals[requestId];
      if (current && current.version > request.version && current.status === decidedStatus) {
        return { status: "applied" };
      }
      if (stale || (current && current.status !== "pending")) {
        return { status: "stale", message: "这项授权已处理或已失效，请查看当前任务状态。" };
      }
      return { status: "failed", message: friendlyErrorMessage(error) };
    } finally {
      set((state) => {
        const next = new Set(state.submitting);
        next.delete(requestId);
        return { submitting: next };
      });
    }
  },
  async cancel(processId) {
    const process = get().processes[processId];
    if (!process || process.status !== "running") return;
    await commandRuntimeApi.cancel(process);
  },
  async setupWindowsSandbox() {
    if (get().settingUpSandbox) return null;
    set({ settingUpSandbox: true, setupError: null });
    try {
      const runtimeStatus = await commandRuntimeApi.setupWindowsSandbox();
      set((state) => ({
        runtimeStatus: preferNewerCommandRuntimeStatus(state.runtimeStatus, runtimeStatus),
      }));
      return runtimeStatus;
    } catch (error) {
      set({ setupError: friendlyErrorMessage(error) });
      return null;
    } finally {
      set({ settingUpSandbox: false });
    }
  },
  markDisconnected() {
    set((state) => ({
      approvals: Object.fromEntries(
        Object.entries(state.approvals).map(([key, request]) => [
          key,
          request.status === "pending" ? { ...request, status: "cancelled" as const } : request,
        ]),
      ),
      processes: Object.fromEntries(
        Object.entries(state.processes).map(([key, process]) => [
          key,
          process.status === "running" ? { ...process, status: "runtime_lost" as const } : process,
        ]),
      ),
    }));
  },
  clear() {
    set({
      approvals: {},
      processes: {},
      submitting: new Set(),
      runtimeStatus: null,
      approvalFence: emptySnapshotFence(),
      processFence: emptySnapshotFence(),
      settingUpSandbox: false,
      setupError: null,
    });
  },
}));

export function pendingCommandApprovalSessionIds(
  approvals: Record<string, CommandApprovalRequest>,
): ReadonlySet<string> {
  return new Set(
    Object.values(approvals)
      .filter((item) => item.status === "pending")
      .map((item) => item.session_id),
  );
}

export function startCommandRuntimeSync(): () => void {
  let disposed = false;
  const unlisteners: UnlistenFn[] = [];
  const refresh = () => useCommandRuntimeStore.getState().refresh().catch(() => undefined);
  const refreshWhenVisible = () => {
    if (!disposed && document.visibilityState !== "hidden") void refresh();
  };
  if (isTauri()) {
    void listen<PushEnvelope>("session://command-runtime", (event) => {
      if (disposed) return;
      useCommandRuntimeStore.getState().handlePush(event.payload);
      if (event.payload.kind === "approval_changed") void refresh();
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
    void listen("session://push-reconnected", () => {
      if (!disposed) void refresh();
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
  }
  window.addEventListener("focus", refreshWhenVisible);
  document.addEventListener("visibilitychange", refreshWhenVisible);
  void refresh();
  return () => {
    disposed = true;
    window.removeEventListener("focus", refreshWhenVisible);
    document.removeEventListener("visibilitychange", refreshWhenVisible);
    unlisteners.forEach((unlisten) => unlisten());
  };
}
