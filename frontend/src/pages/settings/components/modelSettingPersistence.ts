import { setSetting, type ModelSelection } from "@/lib/settings";
import { modelsApi } from "@/services";

export type ModelSettingKey = "chatModel" | "memoryModel";

export const NEW_CONVERSATION_DEFAULT_MODEL_COPY = {
  groupTitle: "默认对话模型",
  groupDescription:
    "用于之后新建的对话和通知文案。当前对话和已经打开的新对话草稿不会跟着切换。",
  modelLabel: "默认模型",
  successTitle: "默认对话模型已保存",
  successDescription:
    "后续新对话和通知将使用该模型；当前对话和已打开的草稿不会切换。",
  errorTitle: "默认对话模型未保存",
} as const;

export type ModelSettingPersistenceResult =
  | { scope: "newConversationDraft" }
  | { scope: "bridge"; applied: boolean; applyError: string | null };

export async function persistModelSettingSelection(
  key: ModelSettingKey,
  selection: ModelSelection,
): Promise<ModelSettingPersistenceResult> {
  await modelsApi.validate(selection.reasoningPreset === undefined ? selection.model : { model: selection.model, reasoningPreset: selection.reasoningPreset });
  if (key === "chatModel") {
    await setSetting(key, selection);
    return { scope: "newConversationDraft" };
  }

  await setSetting(key, selection);
  return {
    scope: "bridge",
    applied: true,
    applyError: null,
  };
}
