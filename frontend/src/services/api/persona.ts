import { invoke } from "@tauri-apps/api/core";
import { http } from "../http";
import { runAgentStreamAtPath } from "../stream";
import type { BaseEvent } from "@ag-ui/core";
import { resolveDefaultConversationRuntimeConfig } from "@/lib/model-config";
import type { SessionEvent } from "@/types/meta";

export type PersonaSource = "builtin" | "user" | "last_valid";
export type PersonaSourceStatus = "builtin" | "valid" | "invalid" | "missing";

export interface PersonaState {
  content: string;
  displayName: string;
  source: PersonaSource;
  sourceStatus: PersonaSourceStatus;
  revision: string;
  editableFileExists: boolean;
  canUndoApply: boolean;
  diagnostic: { code: string; message: string } | null;
}

export interface PersonaCandidate {
  kind: "persona_candidate";
  content: string;
  displayName: string;
  summary: string[];
  baseRevision: string;
}

export interface PersonaEditorSnapshot {
  threadId: string;
  events: SessionEvent[];
  candidate: PersonaCandidate | null;
}

export const personaApi = {
  get(): Promise<PersonaState> {
    return http.get<unknown, PersonaState>("/v1/persona");
  },
  apply(content: string, expectedRevision: string): Promise<PersonaState> {
    return http.put<unknown, PersonaState>("/v1/persona", {
      content,
      expectedRevision,
      source: "guided",
    });
  },
  materialize(): Promise<PersonaState> {
    return http.post<unknown, PersonaState>("/v1/persona/materialize");
  },
  undo(): Promise<PersonaState> {
    return http.post<unknown, PersonaState>("/v1/persona/undo");
  },
  reset(): Promise<PersonaState> {
    return http.delete<unknown, PersonaState>("/v1/persona/customization");
  },
  editor(): Promise<PersonaEditorSnapshot> {
    return http.get<unknown, PersonaEditorSnapshot>("/v1/persona/editor");
  },
  discardEditor(): Promise<PersonaEditorSnapshot> {
    return http.delete<unknown, PersonaEditorSnapshot>("/v1/persona/editor");
  },
};

export async function* runPersonaEditor(
  threadId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<BaseEvent> {
  yield* runAgentStreamAtPath(
    "/ag-ui/persona-editor/run",
    {
      threadId,
      runId: crypto.randomUUID(),
      modelConfig: resolveDefaultConversationRuntimeConfig(),
      text: message,
    },
    { signal },
  );
}

export async function openPersonaTarget(target: "file" | "folder"): Promise<void> {
  await personaApi.materialize();
  await invoke("open_persona_target", { target });
}
