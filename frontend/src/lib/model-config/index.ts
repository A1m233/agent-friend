import { create } from "zustand";
import { getSettingsSnapshot, type ModelProvider, type ModelSelection, type Settings } from "@/lib/settings";
import type { RuntimeConfigGroup } from "@/lib/runtime-config";
import { http } from "@/services/http";

export type InputModality = "text" | "image" | "document";

export interface ModelCapabilities {
  inputModalities: InputModality[];
  acceptedMediaTypes: string[];
  maxAttachmentBytes: number;
  maxTotalAttachmentBytes: number;
  maxAttachmentsPerTurn: number;
  attachments: boolean;
  toolCalling: boolean;
  parallelToolCalls: boolean;
  structuredOutput: boolean;
  multimodalToolResults: boolean;
  /** schema v1 compatibility; schema v2 uses the model-level reasoning object. */
  reasoningReplay?: boolean;
}

export type ReasoningControl = "unsupported" | "toggle" | "effort" | "fixed";

export interface AttachmentInputCapabilities {
  attachments: boolean;
  acceptedMediaTypes: string[];
  maxAttachmentBytes: number;
  maxAttachmentsPerTurn: number;
  maxTotalAttachmentBytes: number;
  maxAttachmentBytesByType?: Record<string, number>;
  nativeMaxAttachmentsPerTurn?: number;
  nativeMaxTotalAttachmentBytes?: number;
  deliveryByMediaType?: Record<string, "session_file" | "native">;
}

export interface ReasoningPresetOption {
  id: string;
  label: string;
}

export interface ModelReasoningDefinition {
  allowCustom?: boolean;
  control: ReasoningControl;
  defaultPreset: string;
  presets: ReasoningPresetOption[];
}

export interface ModelRuntimeConfig {
  model: string;
  reasoningPreset: string;
}

export interface ProviderModelOption {
  id: string;
  label: string;
  routeId: string;
  transportProfile: string;
  processingProfile: string;
  capabilities: ModelCapabilities;
  attachmentInputs?: AttachmentInputCapabilities;
  reasoning?: ModelReasoningDefinition;
}

export interface ProviderOption {
  id: ModelProvider;
  label: string;
  credentialGroup: RuntimeConfigGroup | null;
  defaultChatModel: string;
  defaultMemoryModel: string;
  models: ProviderModelOption[];
}

export interface ModelCatalog {
  schemaVersion: number;
  catalogRevision: string;
  providers: ProviderOption[];
}

interface ModelCatalogState {
  catalog: ModelCatalog | null;
  loading: boolean;
  error: string | null;
  load: (force?: boolean) => Promise<void>;
}

const EMPTY_PROVIDER_OPTIONS: readonly ProviderOption[] = [];
const EMPTY_MODEL_OPTIONS: readonly ProviderModelOption[] = [];

let loadPromise: Promise<void> | null = null;

export const useModelCatalogStore = create<ModelCatalogState>((set, get) => ({
  catalog: null,
  loading: false,
  error: null,
  async load(force = false) {
    if ((!force && get().catalog) || loadPromise) return loadPromise ?? Promise.resolve();
    set({ loading: true, error: null });
    loadPromise = http
      .get<unknown, ModelCatalog>("/v1/models/catalog")
      .then((catalog) => set({ catalog, loading: false }))
      .catch(() => set({ error: "模型能力目录暂时不可用", loading: false }))
      .finally(() => {
        loadPromise = null;
      });
    return loadPromise;
  },
}));

export function modelProvidersFromCatalog(
  catalog: ModelCatalog | null,
): readonly ProviderOption[] {
  return catalog?.providers ?? EMPTY_PROVIDER_OPTIONS;
}

export function providerModelsFromCatalog(
  catalog: ModelCatalog | null,
  provider: ModelProvider,
): readonly ProviderModelOption[] {
  return (
    modelProvidersFromCatalog(catalog).find((item) => item.id === provider)?.models ??
    EMPTY_MODEL_OPTIONS
  );
}

export function modelProviders(): readonly ProviderOption[] {
  return modelProvidersFromCatalog(useModelCatalogStore.getState().catalog);
}

export function providerOption(provider: ModelProvider): ProviderOption | null {
  return modelProviders().find((item) => item.id === provider) ?? null;
}

export function providerModels(provider: ModelProvider): readonly ProviderModelOption[] {
  return providerModelsFromCatalog(useModelCatalogStore.getState().catalog, provider);
}

export function modelOption(modelId: string): ProviderModelOption | null {
  for (const provider of modelProviders()) {
    const model = provider.models.find((item) => item.id === modelId);
    if (model) return model;
  }
  return null;
}

export function defaultModelRuntimeConfig(modelId: string): ModelRuntimeConfig {
  return {
    model: modelId,
    reasoningPreset: modelOption(modelId)?.reasoning?.defaultPreset ?? "none",
  };
}

export function modelRuntimeConfigLabel(config: ModelRuntimeConfig): string {
  const model = modelOption(config.model);
  const modelLabel = model?.label ?? config.model;
  const presetLabel = model?.reasoning?.presets.find(
    (preset) => preset.id === config.reasoningPreset,
  )?.label;
  return presetLabel ? `${modelLabel} · ${presetLabel}` : model?.reasoning?.allowCustom && config.reasoningPreset.startsWith("effort:") ? `${modelLabel} · ${config.reasoningPreset.slice(7)}` : modelLabel;
}

export function credentialGroupForModel(modelId: string): RuntimeConfigGroup | null {
  for (const provider of modelProviders()) {
    if (provider.models.some((model) => model.id === modelId)) {
      return provider.credentialGroup;
    }
  }
  return null;
}

export function isKnownModelSelection(value: ModelSelection): boolean {
  return providerModels(value.provider).some((model) => model.id === value.model);
}

export function defaultModelSelection(
  provider: ModelProvider,
  purpose: "chat" | "memory",
): ModelSelection | null {
  const option = providerOption(provider);
  if (!option) return null;
  return {
    provider: option.id,
    model: purpose === "memory" ? option.defaultMemoryModel : option.defaultChatModel,
  };
}

export function normalizeModelSelection(
  value: ModelSelection,
  purpose: "chat" | "memory",
): ModelSelection {
  if (isKnownModelSelection(value)) return value;
  return defaultModelSelection(value.provider, purpose) ?? value;
}

export function resolveDefaultConversationModel(
  settings: Pick<Settings, "chatModel"> = getSettingsSnapshot(),
): string {
  return settings.chatModel.model;
}

export function resolveDefaultConversationRuntimeConfig(
  settings: Pick<Settings, "chatModel"> = getSettingsSnapshot(),
): ModelRuntimeConfig {
  return { model: settings.chatModel.model, reasoningPreset: settings.chatModel.reasoningPreset ?? defaultModelRuntimeConfig(settings.chatModel.model).reasoningPreset };
}

export function setModelCatalogForTests(catalog: ModelCatalog | null): void {
  useModelCatalogStore.setState({ catalog, loading: false, error: null });
}
