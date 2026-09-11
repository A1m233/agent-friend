import { create } from "zustand";
import { releaseAttachmentDraft, type AttachmentDraft } from "@/services";
import type { ChatAttachment, ChatTextReference } from "@/types/chat";
import type { ModelRuntimeConfig } from "@/lib/model-config";

export const HOME_DRAFT_KEY = "__agent_friend_home_draft__";

export type DraftAttachmentState =
  | { status: "uploading"; localId: string; file: File }
  | {
      status: "ready";
      source: "draft";
      localId: string;
      file: File;
      draft: AttachmentDraft;
    }
  | {
      status: "ready";
      source: "existing";
      localId: string;
      attachment: ChatAttachment;
    }
  | { status: "failed"; localId: string; file: File; message: string }
  | { status: "expired"; localId: string; file: File; message: string };

export interface DraftValidationState {
  modelId: string;
  compatible: boolean;
  reason?: string;
  incompatibleNames: string[];
}

export interface DraftWorkspace {
  /** Validated next-turn choice; never mutates the active run. */
  runtimeConfig?: ModelRuntimeConfig;
  text: string;
  attachments: DraftAttachmentState[];
  references: ChatTextReference[];
  validation: DraftValidationState | null;
  revision: number;
  updatedAt: number;
}

interface DraftSubmission {
  runtimeConfig?: ModelRuntimeConfig;
  runId: string;
  sessionId: string;
  sourceKey: string;
  text: string;
  attachments: DraftAttachmentState[];
  references: ChatTextReference[];
}

interface DraftState {
  workspaces: Record<string, DraftWorkspace>;
  submissions: Record<string, DraftSubmission>;
  initialize: (
    key: string,
    text: string,
    attachments?: DraftAttachmentState[],
    references?: ChatTextReference[],
  ) => void;
  setText: (key: string, text: string) => void;
  setRuntimeConfig: (key: string, config: ModelRuntimeConfig | undefined) => void;
  addAttachment: (key: string, attachment: DraftAttachmentState) => void;
  updateAttachment: (
    key: string,
    localId: string,
    update: (current: DraftAttachmentState) => DraftAttachmentState,
  ) => boolean;
  removeAttachment: (key: string, localId: string) => void;
  addReference: (key: string, reference: ChatTextReference) => void;
  removeReference: (key: string, referenceId: string) => void;
  clearReferences: (key: string) => void;
  setValidation: (key: string, validation: DraftValidationState) => void;
  claimHome: (sessionId: string) => void;
  beginSubmission: (key: string, sessionId: string, runId: string) => void;
  finishSubmission: (runId: string, restore: boolean) => void;
  discard: (key: string) => void;
}

export function draftKey(sessionId: string | null): string {
  return sessionId ?? HOME_DRAFT_KEY;
}

export function editDraftKey(sessionId: string, messageId: string): string {
  return `edit:${sessionId}:${messageId}`;
}

export const EMPTY_DRAFT_WORKSPACE: DraftWorkspace = Object.freeze({
  text: "",
  attachments: [],
  references: [],
  validation: null,
  revision: 0,
  updatedAt: 0,
});

function workspace(state: DraftState, key: string): DraftWorkspace {
  return state.workspaces[key] ?? EMPTY_DRAFT_WORKSPACE;
}

function changed(current: DraftWorkspace, patch: Partial<DraftWorkspace>): DraftWorkspace {
  return {
    ...current,
    ...patch,
    revision: current.revision + 1,
    updatedAt: Date.now(),
  };
}

export const useDraftStore = create<DraftState>((set, get) => ({
  workspaces: {},
  submissions: {},

  initialize(key, text, attachments = [], references = []) {
    set((state) => {
      if (state.workspaces[key]) return state;
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(EMPTY_DRAFT_WORKSPACE, { text, attachments, references }),
        },
      };
    });
  },

  setText(key, text) {
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [key]: changed(workspace(state, key), { text }),
      },
    }));
  },

  setRuntimeConfig(key, runtimeConfig) {
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [key]: changed(workspace(state, key), { runtimeConfig }),
      },
    }));
  },

  addAttachment(key, attachment) {
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [key]: changed(workspace(state, key), {
          attachments: [...workspace(state, key).attachments, attachment],
        }),
      },
    }));
  },

  updateAttachment(key, localId, update) {
    let updated = false;
    set((state) => {
      const current = workspace(state, key);
      if (!current.attachments.some((item) => item.localId === localId)) return state;
      updated = true;
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(current, {
            attachments: current.attachments.map((item) =>
              item.localId === localId ? update(item) : item),
          }),
        },
      };
    });
    return updated;
  },

  removeAttachment(key, localId) {
    const target = workspace(get(), key).attachments.find((item) => item.localId === localId);
    set((state) => {
      const current = workspace(state, key);
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(current, {
            attachments: current.attachments.filter((item) => item.localId !== localId),
          }),
        },
      };
    });
    if (target?.status === "ready" && target.source === "draft") {
      void releaseAttachmentDraft(target.draft.draftId);
    }
  },

  addReference(key, reference) {
    set((state) => {
      const current = workspace(state, key);
      if (current.references.some((item) => item.referenceId === reference.referenceId)) {
        return state;
      }
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(current, { references: [...current.references, reference] }),
        },
      };
    });
  },

  removeReference(key, referenceId) {
    set((state) => {
      const current = workspace(state, key);
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(current, {
            references: current.references.filter((item) => item.referenceId !== referenceId),
          }),
        },
      };
    });
  },

  clearReferences(key) {
    set((state) => {
      const current = workspace(state, key);
      if (current.references.length === 0) return state;
      return {
        workspaces: {
          ...state.workspaces,
          [key]: changed(current, { references: [] }),
        },
      };
    });
  },

  setValidation(key, validation) {
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [key]: { ...workspace(state, key), validation },
      },
    }));
  },

  claimHome(sessionId) {
    set((state) => {
      const home = state.workspaces[HOME_DRAFT_KEY];
      if (!home) return state;
      const workspaces = { ...state.workspaces, [sessionId]: home };
      delete workspaces[HOME_DRAFT_KEY];
      return { workspaces };
    });
  },

  beginSubmission(key, sessionId, runId) {
    set((state) => {
      const current = workspace(state, key);
      const workspaces = { ...state.workspaces };
      delete workspaces[key];
      return {
        workspaces,
        submissions: {
          ...state.submissions,
          [runId]: {
            runtimeConfig: current.runtimeConfig,
            runId,
            sessionId,
            sourceKey: key,
            text: current.text,
            attachments: current.attachments,
            references: current.references,
          },
        },
      };
    });
  },

  finishSubmission(runId, restore) {
    set((state) => {
      const submission = state.submissions[runId];
      if (!submission) return state;
      const submissions = { ...state.submissions };
      delete submissions[runId];
      if (!restore) return { submissions };
      const targetKey = submission.sessionId;
      const current = workspace(state, targetKey);
      const existingIds = new Set(current.attachments.map((item) => item.localId));
      const existingReferenceIds = new Set(
        current.references.map((item) => item.referenceId),
      );
      const restoredText = [submission.text, current.text].filter(Boolean).join("\n");
      return {
        submissions,
        workspaces: {
          ...state.workspaces,
          [targetKey]: changed(current, {
            runtimeConfig: current.runtimeConfig ?? submission.runtimeConfig,
            text: restoredText,
            attachments: [
              ...submission.attachments.filter((item) => !existingIds.has(item.localId)),
              ...current.attachments,
            ],
            references: [
              ...submission.references.filter(
                (item) => !existingReferenceIds.has(item.referenceId),
              ),
              ...current.references,
            ],
          }),
        },
      };
    });
  },

  discard(key) {
    const current = workspace(get(), key);
    for (const item of current.attachments) {
      if (item.status === "ready" && item.source === "draft") {
        void releaseAttachmentDraft(item.draft.draftId);
      }
    }
    set((state) => {
      const workspaces = { ...state.workspaces };
      delete workspaces[key];
      return { workspaces };
    });
  },
}));
