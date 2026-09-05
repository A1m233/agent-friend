/** Projection only: never consult current model settings or Provider catalog. */
import type { AssistantPhase, AssistantPhaseState, ChatMessage, MessageBlock, TextBlock } from "@/types/chat";

export function normalizeAssistantPhase(value: unknown): AssistantPhase {
  return value === "commentary" || value === "final_answer" ? value : "unknown";
}

export function parseAssistantPhaseState(value: unknown): AssistantPhaseState | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;
  if (raw.mode !== "native" && raw.mode !== "legacy") return undefined;
  const validation = raw.validation === "valid" || raw.validation === "missing_final"
    || raw.validation === "conflict" ? raw.validation : "pending";
  return { mode: raw.mode, validation };
}

export function applyAssistantPhase(msg: ChatMessage, value: unknown, runId?: string): ChatMessage {
  if (runId && msg.runId && runId !== msg.runId) return msg;
  const assistantPhase = parseAssistantPhaseState(value);
  return assistantPhase ? { ...msg, assistantPhase } : msg;
}

export function setAssistantItem(
  blocks: MessageBlock[], mid: string, phase: unknown, itemIndex: unknown,
): MessageBlock[] {
  const previous = blocks.find((block): block is TextBlock => block.kind === "text" && block.mid === mid);
  if (previous?.itemIndex !== undefined) {
    return blocks.map((block) => block === previous
      ? { ...previous, phase: normalizeAssistantPhase(phase) } : block);
  }
  const block: TextBlock = {
    kind: "text", mid, text: previous?.text ?? "", phase: normalizeAssistantPhase(phase),
    itemIndex: typeof itemIndex === "number" && Number.isInteger(itemIndex) && itemIndex >= 0
      ? itemIndex : undefined,
  };
  const result = blocks.filter((item) => item.kind !== "text" || item.mid !== mid);
  const next = result.findIndex((item) => item.kind === "text" && item.itemIndex !== undefined
    && block.itemIndex !== undefined && item.itemIndex > block.itemIndex);
  if (next < 0) result.push(block);
  else result.splice(next, 0, block);
  return result;
}

export function updateAssistantItemPhase(blocks: MessageBlock[], mid: string, phase: unknown): MessageBlock[] {
  return blocks.map((block) => block.kind === "text" && block.mid === mid
    ? { ...block, phase: normalizeAssistantPhase(phase) } : block);
}

export function canDiscloseExecution(message: ChatMessage): boolean {
  return message.assistantPhase?.mode === "native" && message.assistantPhase.validation !== "conflict";
}

export function shouldCollapseExecution(message: ChatMessage): boolean {
  return canDiscloseExecution(message) && message.assistantPhase?.validation === "valid"
    && message.status === "complete" && message.runOutcome === "completed"
    && message.blocks.some((block) => block.kind === "text" && block.phase === "final_answer" && !!block.text.trim());
}
