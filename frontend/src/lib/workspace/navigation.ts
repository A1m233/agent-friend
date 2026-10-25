import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { create } from "zustand";

import type { SettingsOpenRequest } from "@/lib/settings/navigation";
import { isTauri } from "@/utils/tauri";

export const WORKSPACE_NAVIGATE_EVENT = "workspace://navigate";

export type WorkspaceTarget =
  | "chat"
  | "settings"
  | "im-connect"
  | "scheduled-tasks"
  | "todos"
  | "memory-inspector";

export interface WorkspaceNavigationEnvelope {
  requestId: number;
  target: WorkspaceTarget;
  chat?: {
    newConversation: boolean;
    sessionId?: string;
  };
  settings?: SettingsOpenRequest;
}

export interface WorkspaceNavigationSnapshot {
  target: WorkspaceTarget;
  lastRequestId: number;
  visitedTargets: readonly WorkspaceTarget[];
  chatNavigation: WorkspaceNavigationEnvelope | null;
  settingsNavigation: WorkspaceNavigationEnvelope | null;
}

const INITIAL_SNAPSHOT: WorkspaceNavigationSnapshot = {
  target: "chat",
  lastRequestId: 0,
  visitedTargets: ["chat"],
  chatNavigation: null,
  settingsNavigation: null,
};

function includeVisitedTarget(
  targets: readonly WorkspaceTarget[],
  target: WorkspaceTarget,
): readonly WorkspaceTarget[] {
  return targets.includes(target) ? targets : [...targets, target];
}

export function navigateWorkspaceSnapshot(
  snapshot: WorkspaceNavigationSnapshot,
  target: WorkspaceTarget,
): WorkspaceNavigationSnapshot {
  return {
    ...snapshot,
    target,
    visitedTargets: includeVisitedTarget(snapshot.visitedTargets, target),
  };
}

export function applyWorkspaceNavigationEnvelope(
  snapshot: WorkspaceNavigationSnapshot,
  envelope: WorkspaceNavigationEnvelope,
): WorkspaceNavigationSnapshot {
  if (envelope.requestId <= snapshot.lastRequestId) return snapshot;
  return {
    target: envelope.target,
    lastRequestId: envelope.requestId,
    visitedTargets: includeVisitedTarget(snapshot.visitedTargets, envelope.target),
    chatNavigation:
      envelope.target === "chat" && (envelope.chat?.newConversation || envelope.chat?.sessionId)
        ? envelope
        : snapshot.chatNavigation,
    settingsNavigation:
      envelope.target === "settings" && envelope.settings
        ? envelope
        : snapshot.settingsNavigation,
  };
}

interface WorkspaceNavigationStore extends WorkspaceNavigationSnapshot {
  navigate: (target: WorkspaceTarget) => void;
  applyEnvelope: (envelope: WorkspaceNavigationEnvelope) => void;
}

export const useWorkspaceNavigationStore = create<WorkspaceNavigationStore>((set) => ({
  ...INITIAL_SNAPSHOT,
  navigate: (target) => set((state) => navigateWorkspaceSnapshot(state, target)),
  applyEnvelope: (envelope) =>
    set((state) => applyWorkspaceNavigationEnvelope(state, envelope)),
}));

export function startWorkspaceNavigationSync(): () => void {
  if (!isTauri()) return () => {};

  let disposed = false;
  let unlisten: UnlistenFn | null = null;
  const applyEnvelope = (envelope: WorkspaceNavigationEnvelope) => {
    useWorkspaceNavigationStore.getState().applyEnvelope(envelope);
  };

  void (async () => {
    const stop = await listen<WorkspaceNavigationEnvelope>(
      WORKSPACE_NAVIGATE_EVENT,
      (event) => applyEnvelope(event.payload),
    );
    if (disposed) {
      stop();
      return;
    }
    unlisten = stop;

    const pending = await invoke<WorkspaceNavigationEnvelope | null>(
      "consume_pending_workspace_navigation",
    );
    if (!disposed && pending) applyEnvelope(pending);
  })().catch((error) => {
    console.warn("workspace navigation sync failed", error);
  });

  return () => {
    disposed = true;
    unlisten?.();
  };
}
