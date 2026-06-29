import { getSettingsSnapshot, type DeepseekModel, type Settings } from "@/lib/settings";

export const DEEPSEEK_MODELS = [
  "deepseek/deepseek-v4-flash",
  "deepseek/deepseek-v4-pro",
] as const satisfies readonly DeepseekModel[];

const DEFAULT_DEEPSEEK_MODEL: DeepseekModel = "deepseek/deepseek-v4-pro";

export function isDeepseekModel(value: unknown): value is DeepseekModel {
  return typeof value === "string" && DEEPSEEK_MODELS.includes(value as DeepseekModel);
}

export function resolveDefaultConversationModel(
  settings: Pick<Settings, "deepseekModel"> = getSettingsSnapshot(),
): DeepseekModel {
  return isDeepseekModel(settings.deepseekModel)
    ? settings.deepseekModel
    : DEFAULT_DEEPSEEK_MODEL;
}
