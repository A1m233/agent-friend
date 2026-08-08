import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { create } from "zustand";
import {
  filesystemApprovalsApi,
  type FilesystemApprovalRequest,
} from "@/services/api/filesystem";
import type { PushEnvelope } from "@/types/push";
import { isTauri } from "@/utils/tauri";

export type FilesystemApprovalUiStatus =
  | "pending"
  | "submitting"
  | "approved"
  | "denied"
  | "expired"
  | "cancelled"
  | "interrupted";

export interface FilesystemApprovalEntry {
  request: FilesystemApprovalRequest;
  status: FilesystemApprovalUiStatus;
  version: number;
  recoverable: boolean;
  error?: string;
}

export interface FilesystemApprovalAttention {
  attention_id: string;
  type: "needs_filesystem_approval";
  session_id: string;
  run_id: string;
  request_ids: string[];
  status: "pending";
  version: number;
}

interface FilesystemApprovalState {
  entries: Record<string, FilesystemApprovalEntry>;
  refresh: () => Promise<void>;
  merge: (request: FilesystemApprovalRequest) => void;
  decide: (requestId: string, approved: boolean) => Promise<void>;
  markDisconnected: () => void;
  clear: () => void;
}

function lifecycleStatus(request: FilesystemApprovalRequest): FilesystemApprovalUiStatus {
  return request.status;
}

function mergeRequest(
  entries: Record<string, FilesystemApprovalEntry>,
  request: FilesystemApprovalRequest,
): Record<string, FilesystemApprovalEntry> {
  const existing = entries[request.request_id];
  if (existing && request.version < existing.version) return entries;
  if (
    existing
    && request.version === existing.version
    && request.status === "pending"
    && existing.status !== "pending"
    && existing.status !== "submitting"
    && !existing.recoverable
  ) {
    return entries;
  }
  const status =
    request.status === "pending" && existing?.status === "submitting"
      ? "submitting"
      : lifecycleStatus(request);
  return {
    ...entries,
    [request.request_id]: {
      request,
      status,
      version: request.version,
      recoverable: false,
      error: existing?.error,
    },
  };
}

export const useFilesystemApprovalsStore = create<FilesystemApprovalState>((set, get) => ({
  entries: {},
  async refresh() {
    const pending = await filesystemApprovalsApi.pending();
    set((state) => {
      let next = state.entries;
      const activeIds = new Set(pending.map((request) => request.request_id));
      for (const request of pending) {
        const wasRecoverable = Boolean(next[request.request_id]?.recoverable);
        next = mergeRequest(next, request);
        const restored = next[request.request_id];
        if (wasRecoverable && restored) {
          next = {
            ...next,
            [request.request_id]: { ...restored, status: "pending", recoverable: false },
          };
        }
      }
      for (const [requestId, entry] of Object.entries(next)) {
        if (activeIds.has(requestId)) continue;
        if (entry.status === "pending" || entry.status === "submitting") {
          next = {
            ...next,
            [requestId]: { ...entry, status: "interrupted", recoverable: false },
          };
        }
      }
      return { entries: next };
    });
  },
  merge(request) {
    set((state) => ({ entries: mergeRequest(state.entries, request) }));
  },
  async decide(requestId, approved) {
    const entry = get().entries[requestId];
    if (!entry || entry.status !== "pending") return;
    set((state) => ({
      entries: {
        ...state.entries,
        [requestId]: { ...entry, status: "submitting", error: undefined },
      },
    }));
    try {
      await filesystemApprovalsApi.decide(entry.request, approved);
      set((state) => {
        const current = state.entries[requestId];
        if (!current || current.version > entry.version) return state;
        const status = approved ? "approved" : "denied";
        return {
          entries: {
            ...state.entries,
            [requestId]: {
              ...entry,
              request: { ...entry.request, status, version: entry.version + 1 },
              status,
              version: entry.version + 1,
              recoverable: false,
            },
          },
        };
      });
    } catch (error) {
      set((state) => ({
        entries: {
          ...state.entries,
          [requestId]: {
            ...entry,
            status: "interrupted",
            recoverable: false,
            error: error instanceof Error ? error.message : "授权请求已失效",
          },
        },
      }));
    }
  },
  markDisconnected() {
    set((state) => ({
      entries: Object.fromEntries(
        Object.entries(state.entries).map(([requestId, entry]) => [
          requestId,
          entry.status === "pending" || entry.status === "submitting"
            ? { ...entry, status: "interrupted" as const, recoverable: true }
            : entry,
        ]),
      ),
    }));
  },
  clear() {
    set({ entries: {} });
  },
}));

function approvalFromEnvelope(env: PushEnvelope): FilesystemApprovalRequest | null {
  if (env.kind !== "filesystem_approval_changed" || !env.filesystem_approval) return null;
  const approval = env.filesystem_approval as Partial<FilesystemApprovalRequest>;
  if (
    typeof approval.request_id !== "string"
    || typeof approval.session_id !== "string"
    || typeof approval.run_id !== "string"
    || typeof approval.tool_call_id !== "string"
    || !Number.isSafeInteger(approval.version)
    || !["pending", "approved", "denied", "expired", "cancelled"].includes(
      String(approval.status),
    )
    || !Array.isArray(approval.items)
    || !Array.isArray(approval.actions)
  ) {
    return null;
  }
  return approval as FilesystemApprovalRequest;
}

export function handleFilesystemApprovalPush(env: PushEnvelope): void {
  const approval = approvalFromEnvelope(env);
  if (approval) useFilesystemApprovalsStore.getState().merge(approval);
}

export function startFilesystemApprovalSync(): () => void {
  let disposed = false;
  const unlisteners: UnlistenFn[] = [];
  const refresh = async () => {
    try {
      await useFilesystemApprovalsStore.getState().refresh();
    } catch {
      // Transport loss never becomes an approval decision. Connection state
      // marks cards interrupted until push/REST proves the broker still owns them.
    }
  };
  if (isTauri()) {
    void listen<PushEnvelope>("session://filesystem-approval", (event) => {
      if (!disposed) handleFilesystemApprovalPush(event.payload);
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
    void listen("session://push-reconnected", () => {
      if (!disposed) void refresh();
    }).then((unlisten) => disposed ? unlisten() : unlisteners.push(unlisten));
  }
  void refresh();
  const timer = window.setInterval(() => {
    if (!disposed) void refresh();
  }, isTauri() ? 5000 : 300);
  return () => {
    disposed = true;
    window.clearInterval(timer);
    unlisteners.forEach((unlisten) => unlisten());
  };
}

export function filesystemApprovalAttentions(
  entries: Record<string, FilesystemApprovalEntry>,
): FilesystemApprovalAttention[] {
  const grouped = new Map<string, FilesystemApprovalEntry[]>();
  for (const entry of Object.values(entries)) {
    if (entry.status !== "pending" && entry.status !== "submitting") continue;
    const list = grouped.get(entry.request.session_id) ?? [];
    list.push(entry);
    grouped.set(entry.request.session_id, list);
  }
  return [...grouped.entries()].map(([sessionId, sessionEntries]) => ({
    attention_id: `filesystem-approval:${sessionId}`,
    type: "needs_filesystem_approval",
    session_id: sessionId,
    run_id: sessionEntries[0].request.run_id,
    request_ids: sessionEntries.map((entry) => entry.request.request_id).sort(),
    status: "pending",
    version: Math.max(...sessionEntries.map((entry) => entry.version)),
  }));
}

export function pendingFilesystemApprovalSessionIds(
  entries: Record<string, FilesystemApprovalEntry>,
): ReadonlySet<string> {
  return new Set(filesystemApprovalAttentions(entries).map((attention) => attention.session_id));
}
