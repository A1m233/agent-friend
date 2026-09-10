import { invoke } from "@tauri-apps/api/core";
import {
  getSettingsSnapshot,
  type CustomConnection,
  type ModelSelection,
} from "@/lib/settings";
import { useModelCatalogStore } from "./index";

export function customModelReference(
  connection: string,
  model: string,
): string {
  return `custom/${connection}/${model}`;
}

export async function saveConnections(
  expectedRevision: number,
  connections: CustomConnection[],
  apiKeys: Record<string, string>,
  defaults?: { chatModel: ModelSelection; memoryModel: ModelSelection },
): Promise<{
  persisted: boolean;
  applied: boolean;
  cleanupComplete?: boolean;
}> {
  const snapshot = getSettingsSnapshot();
  const result = await invoke<{
    persisted: boolean;
    applied: boolean;
    cleanupComplete?: boolean;
  }>("save_model_connections", {
    payload: {
      expectedRevision,
      connections,
      apiKeys,
      chatModel: defaults?.chatModel ?? snapshot.chatModel,
      memoryModel: defaults?.memoryModel ?? snapshot.memoryModel,
    },
  });
  await useModelCatalogStore.getState().load(true);
  return result;
}

export interface DiscoveredModel {
  id: string;
  contextWindow: number | null;
  contextSource: "endpoint" | "catalog" | null;
  contextLabel: string;
}
export interface ProbeCheck {
  status: "passed" | "failed" | "skipped";
  code?: string;
}
export interface ProbeResult {
  metadata: DiscoveredModel;
  generation: ProbeCheck;
  tools: ProbeCheck;
}

async function inspect<T>(payload: Record<string, unknown>): Promise<T> {
  const result = await invoke<T & { error?: string }>(
    "inspect_model_connection",
    { payload },
  );
  if (result.error) throw new Error(result.error);
  return result;
}
function draftPayload(draft: CustomConnection, key: string) {
  return {
    connectionId: draft.id,
    baseUrl: draft.baseUrl.trim(),
    protocol: draft.protocol,
    apiKey: key,
  };
}
export async function discoverModels(
  draft: CustomConnection,
  key: string,
): Promise<DiscoveredModel[]> {
  const result = await inspect<{ models: DiscoveredModel[] }>({
    ...draftPayload(draft, key),
    mode: "discover",
  });
  return result.models;
}
export async function probeModel(
  draft: CustomConnection,
  key: string,
  model: string,
): Promise<ProbeResult> {
  return inspect<ProbeResult>({
    ...draftPayload(draft, key),
    mode: "probe",
    model,
  });
}
export async function readModelMetadata(
  model: string,
): Promise<DiscoveredModel> {
  const result = await inspect<{ metadata: DiscoveredModel }>({
    mode: "metadata",
    model,
  });
  return result.metadata;
}

export async function retryConnections(): Promise<void> {
  await invoke("retry_model_connections");
  await useModelCatalogStore.getState().load(true);
}
