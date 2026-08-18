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

interface CommandRuntimeState {
  approvals: Record<string, CommandApprovalRequest>;
  processes: Record<string, CommandProcessSnapshot>;
  submitting: ReadonlySet<string>;
  runtimeStatus: CommandRuntimeStatus | null;
  settingUpSandbox: boolean;
  setupError: string | null;
  refresh: () => Promise<void>;
  handlePush: (envelope: PushEnvelope) => void;
  decide: (requestId: string, allow: boolean) => Promise<void>;
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
  settingUpSandbox: false,
  setupError: null,
  async refresh() {
    const [approvals, processes, runtimeStatus] = await Promise.all([
      commandRuntimeApi.pending(),
      commandRuntimeApi.processes(),
      commandRuntimeApi.status(),
    ]);
    set((state) => ({
      approvals: {
        ...state.approvals,
        ...Object.fromEntries(approvals.map((item) => [item.request_id, item])),
      },
      processes: {
        ...state.processes,
        ...Object.fromEntries(processes.map((item) => [item.process_id, item])),
      },
      runtimeStatus: preferNewerCommandRuntimeStatus(state.runtimeStatus, runtimeStatus),
    }));
  },
  handlePush(envelope) {
    if (envelope.kind === "approval_changed" && validApproval(envelope.approval)) {
      const request = envelope.approval;
      set((state) => ({
        approvals: {
          ...state.approvals,
          [request.request_id]: request.version >= (state.approvals[request.request_id]?.version ?? 0)
            ? request
            : state.approvals[request.request_id],
        },
      }));
    }
    if (envelope.kind === "command_process_changed" && validProcess(envelope.command_process)) {
      const process = envelope.command_process;
      set((state) => ({
        processes: {
          ...state.processes,
          [process.process_id]: process.version >= (state.processes[process.process_id]?.version ?? 0)
            ? process
            : state.processes[process.process_id],
        },
      }));
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
  async decide(requestId, allow) {
    const request = get().approvals[requestId];
    if (!request || request.status !== "pending" || get().submitting.has(requestId)) return;
    set((state) => ({ submitting: new Set(state.submitting).add(requestId) }));
    try {
      await commandRuntimeApi.decide(request, allow);
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
