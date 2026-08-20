import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { create } from "zustand";
import {
  applyRuntimeCredentialGroup,
  cancelRuntimeCredentialOperation,
  confirmRuntimeCredentialSave,
  getRuntimeCredentialGroups,
  retryRuntimeCredentialApply,
  retryRuntimeCredentialValidation,
  reportRuntimeCredentialAuthFailure,
  type RuntimeConfigGroup,
  type RuntimeCredentialFieldMutation,
  type RuntimeCredentialGroupSnapshot,
} from "@/lib/runtime-config";

export const RUNTIME_CREDENTIAL_CHANGED_EVENT = "runtime-credential://changed";

interface RuntimeCredentialState {
  groups: RuntimeCredentialGroupSnapshot[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  applyGroup: (
    group: RuntimeConfigGroup,
    changes: RuntimeCredentialFieldMutation[],
  ) => Promise<RuntimeCredentialGroupSnapshot>;
  confirmSave: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  cancelOperation: (operationId: string) => Promise<RuntimeCredentialGroupSnapshot>;
  retryApply: (group: RuntimeConfigGroup) => Promise<RuntimeCredentialGroupSnapshot>;
  retryValidation: (group: RuntimeConfigGroup) => Promise<RuntimeCredentialGroupSnapshot>;
  reportAuthFailure: (
    group: RuntimeConfigGroup,
    credentialRevision: string,
  ) => Promise<RuntimeCredentialGroupSnapshot>;
}

function replaceGroup(
  groups: RuntimeCredentialGroupSnapshot[],
  next: RuntimeCredentialGroupSnapshot,
): RuntimeCredentialGroupSnapshot[] {
  return groups.some((group) => group.group === next.group)
    ? groups.map((group) => (group.group === next.group ? next : group))
    : [...groups, next];
}

async function runGroupOperation(
  operation: () => Promise<RuntimeCredentialGroupSnapshot>,
): Promise<RuntimeCredentialGroupSnapshot> {
  try {
    const group = await operation();
    useRuntimeCredentialStore.setState((state) => ({
      groups: replaceGroup(state.groups, group),
      error: null,
    }));
    return group;
  } catch (error) {
    useRuntimeCredentialStore.setState({ error: String(error) });
    throw error;
  }
}

export const useRuntimeCredentialStore = create<RuntimeCredentialState>((set) => ({
  groups: [],
  loading: false,
  error: null,
  async refresh() {
    set({ loading: true, error: null });
    try {
      const snapshot = await getRuntimeCredentialGroups();
      set({ groups: snapshot.groups, loading: false, error: null });
    } catch (error) {
      set({ loading: false, error: String(error) });
    }
  },
  applyGroup(group, changes) {
    return runGroupOperation(() => applyRuntimeCredentialGroup(group, changes));
  },
  confirmSave(operationId) {
    return runGroupOperation(() => confirmRuntimeCredentialSave(operationId));
  },
  cancelOperation(operationId) {
    return runGroupOperation(() => cancelRuntimeCredentialOperation(operationId));
  },
  retryApply(group) {
    return runGroupOperation(() => retryRuntimeCredentialApply(group));
  },
  retryValidation(group) {
    return runGroupOperation(() => retryRuntimeCredentialValidation(group));
  },
  reportAuthFailure(group, credentialRevision) {
    return runGroupOperation(() =>
      reportRuntimeCredentialAuthFailure(group, credentialRevision),
    );
  },
}));

let unlistenPromise: Promise<UnlistenFn> | null = null;
let subscribers = 0;

export function startRuntimeCredentialSync(): () => void {
  subscribers += 1;
  if (!unlistenPromise) {
    unlistenPromise = listen<RuntimeCredentialGroupSnapshot>(
      RUNTIME_CREDENTIAL_CHANGED_EVENT,
      (event) => {
        useRuntimeCredentialStore.setState((state) => ({
          groups: replaceGroup(state.groups, event.payload),
        }));
      },
    );
    void useRuntimeCredentialStore.getState().refresh();
  }
  return () => {
    subscribers = Math.max(0, subscribers - 1);
    if (subscribers === 0 && unlistenPromise) {
      const current = unlistenPromise;
      unlistenPromise = null;
      void current.then((unlisten) => unlisten());
    }
  };
}
