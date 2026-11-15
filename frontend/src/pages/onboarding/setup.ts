import { invoke } from "@tauri-apps/api/core";
import type { ModelSelection } from "@/lib/settings";

export const guidePrompt =
  `请帮我配置 agent-friend。先读官方 Guide Skill：
https://github.com/A1m233/agent-friend-guide/tree/main/skills/agent-friend-guide

请按我的版本带我连接模型服务（含自定义连接 BYOK），选好对话和记忆模型。如果能操作本机，请先用 --af-setup status 查看配置。API Key 我会自己在 AF 里填。等能聊天了，再问我要不要设置语音、人格和自主陪伴。`;

export interface SetupStatus {
  models: (ModelSelection & { label: string })[];
  chatModel: ModelSelection;
  memoryModel: ModelSelection;
  chatReady: boolean;
  memoryReady: boolean;
}

export const readStatus = () => invoke<SetupStatus>("get_onboarding_status");
export const selectModel = (purpose: "chat" | "memory", { provider, model, reasoningPreset }: ModelSelection) =>
  invoke<void>("select_onboarding_model", { purpose, model: { provider, model, reasoningPreset } });

export type Step =
  | "welcome"
  | "connect"
  | "chat"
  | "memory"
  | "ready"
  | "voice"
  | "persona"
  | "companionship";

export function missingStep(status: SetupStatus): Step | null {
  if (!status.models.length) return "connect";
  if (!status.chatReady) return "chat";
  if (!status.memoryReady) return "memory";
  return null;
}
