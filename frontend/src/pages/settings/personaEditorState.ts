import { EventType, type BaseEvent, type ToolCallResultEvent } from "@ag-ui/core";
import type { PersonaCandidate } from "@/services";
import type { ChatMessage } from "@/types/chat";

export function personaCandidateFromEvent(event: BaseEvent): PersonaCandidate | null {
  if (event.type !== EventType.TOOL_CALL_RESULT) return null;
  const content = (event as ToolCallResultEvent).content;
  try {
    const parsed = JSON.parse(content) as Partial<PersonaCandidate>;
    if (
      parsed.kind === "persona_candidate" &&
      typeof parsed.content === "string" &&
      typeof parsed.displayName === "string" &&
      Array.isArray(parsed.summary) &&
      parsed.summary.every((item) => typeof item === "string") &&
      typeof parsed.baseRevision === "string"
    ) {
      return parsed as PersonaCandidate;
    }
  } catch {
    // 普通工具结果不是候选人格。
  }
  return null;
}

/** 提案工具是编辑协议细节：候选正文在约束区展示，不在对话里重复渲染工具卡。 */
export function visiblePersonaEditorMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages
    .map((message) => ({
      ...message,
      blocks: message.blocks.filter(
        (block) => block.kind !== "tool" || block.name !== "propose_persona",
      ),
    }))
    .filter(
      (message) =>
        message.role === "user" ||
        message.blocks.length > 0 ||
        message.status === "streaming" ||
        message.status === "error",
    );
}
