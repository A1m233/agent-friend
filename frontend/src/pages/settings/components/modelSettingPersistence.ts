import { persistBridgeRuntimeSetting } from "@/lib/runtime-config";
import { setSetting, type ModelSelection } from "@/lib/settings";
import { modelsApi } from "@/services";

export type ModelSettingKey = "chatModel" | "memoryModel";

export const NEW_CONVERSATION_DEFAULT_MODEL_COPY = {
  groupTitle: "新对话默认模型",
  groupDescription:
    "只决定之后新建对话的初始选择；当前对话和已经打开的新对话草稿不会跟着切换。",
  modelLabel: "默认模型",
  successTitle: "新对话默认模型已保存",
  successDescription:
    "下次新建对话时将默认选择该模型；当前对话和已打开的草稿不会切换。",
  errorTitle: "新对话默认模型未保存",
} as const;

export type ModelSettingPersistenceResult =
  | { scope: "newConversationDraft" }
  | { scope: "bridge"; applied: boolean; applyError: string | null };

export async function persistModelSettingSelection(
  key: ModelSettingKey,
  selection: ModelSelection,
): Promise<ModelSettingPersistenceResult> {
  await modelsApi.validate(selection.model);
  if (key === "chatModel") {
    await setSetting(key, selection);
    return { scope: "newConversationDraft" };
  }

  const result = await persistBridgeRuntimeSetting(() => setSetting(key, selection));
  return {
    scope: "bridge",
    applied: result.applied,
    applyError: result.applyError,
  };
}
