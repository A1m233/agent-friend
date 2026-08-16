import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { create } from "zustand";
import {
  commandRuntimeApi,
  type CommandApprovalRequest,
  type CommandProcessSnapshot,
  type CommandRuntimeStatus,
} from "@/services/api/commandRuntime";
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
  setupWindowsSandbox: () => Promise<void>;
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
      runtimeStatus,
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
    if (get().settingUpSandbox) return;
    set({ settingUpSandbox: true, setupError: null });
    try {
      const runtimeStatus = await commandRuntimeApi.setupWindowsSandbox();
      set({ runtimeStatus });
    } catch (error) {
      set({ setupError: error instanceof Error ? error.message : String(error) });
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
  if (isTauri()) {
    void listen<PushEnvelope>("session://command-runtime", (event) => {
      if (!disposed) useCommandRuntimeStore.getState().handlePush(event.payload);
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
    void listen("session://push-reconnected", () => {
      if (!disposed) void refresh();
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
  }
  void refresh();
  return () => {
    disposed = true;
    unlisteners.forEach((unlisten) => unlisten());
  };
}
