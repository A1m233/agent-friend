import {
  getSettingsSnapshot,
  type ModelProvider,
  type ModelSelection,
  type Settings,
} from "@/lib/settings";

export interface ProviderModelOption {
  id: string;
  label: string;
}

export interface ProviderOption {
  id: ModelProvider;
  label: string;
  defaultChatModel: string;
  defaultMemoryModel: string;
  models: readonly ProviderModelOption[];
}

export const MODEL_PROVIDERS: readonly ProviderOption[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    defaultChatModel: "deepseek/deepseek-v4-flash",
    defaultMemoryModel: "deepseek/deepseek-v4-pro",
    models: [
      { id: "deepseek/deepseek-v4-flash", label: "deepseek/deepseek-v4-flash" },
      { id: "deepseek/deepseek-v4-pro", label: "deepseek/deepseek-v4-pro" },
    ],
  },
  {
    id: "kimi",
    label: "Kimi",
    defaultChatModel: "kimi/kimi-k2.6",
    defaultMemoryModel: "kimi/kimi-k2.6",
    models: [
      { id: "kimi/kimi-k2.6", label: "kimi-k2.6" },
      { id: "kimi/kimi-k2.5", label: "kimi-k2.5" },
      { id: "kimi/kimi-k2.7-code", label: "kimi-k2.7-code" },
      {
        id: "kimi/kimi-k2.7-code-highspeed",
        label: "kimi-k2.7-code-highspeed",
      },
    ],
  },
] as const;

const PROVIDER_BY_ID = new Map<ModelProvider, ProviderOption>(
  MODEL_PROVIDERS.map((provider) => [provider.id, provider]),
);

export function providerOption(provider: ModelProvider): ProviderOption {
  return PROVIDER_BY_ID.get(provider) ?? MODEL_PROVIDERS[0];
}

export function providerModels(provider: ModelProvider): readonly ProviderModelOption[] {
  return providerOption(provider).models;
}

export function isKnownModelSelection(value: ModelSelection): boolean {
  return providerModels(value.provider).some((model) => model.id === value.model);
}

export function defaultModelSelection(
  provider: ModelProvider,
  purpose: "chat" | "memory",
): ModelSelection {
  const option = providerOption(provider);
  return {
    provider: option.id,
    model: purpose === "memory" ? option.defaultMemoryModel : option.defaultChatModel,
  };
}

export function normalizeModelSelection(
  value: ModelSelection,
  purpose: "chat" | "memory",
): ModelSelection {
  if (isKnownModelSelection(value)) {
    return value;
  }
  return defaultModelSelection(value.provider, purpose);
}

export function resolveDefaultConversationModel(
  settings: Pick<Settings, "chatModel"> = getSettingsSnapshot(),
): string {
  return normalizeModelSelection(settings.chatModel, "chat").model;
}
