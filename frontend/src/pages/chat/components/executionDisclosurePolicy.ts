import type { AssistantPhase, ChatMessage, TextBlock } from "@/types/chat";
import { normalizeAssistantPhase } from "@/stores/assistantPhase";
import type { MessagePresentationSegment } from "./toolActivityPresentation";
import type { SocialAuthRequestStatus } from "@/services";

export function socialAuthNeedsUserAction(status: SocialAuthRequestStatus | undefined): boolean {
  return status === "pending" || status === "login_in_progress";
}

export interface ExecutionSection {
  id: string;
  execution: boolean;
  indexes: number[];
  textIds: string[];
}

export interface ExecutionDisclosurePolicy {
  /** A view decision, never a replacement for the provider's persisted phase. */
  source: "native" | "inferred" | "mixed" | "none";
  /** The last visible item is not necessarily a provider final_answer. */
  retainedTextId?: string;
  processTextIds: string[];
  collapse: boolean;
}

export function executionDisclosurePolicy(message: ChatMessage): ExecutionDisclosurePolicy {
  const unavailable: ExecutionDisclosurePolicy = { source: "none", processTextIds: [], collapse: false };
  if (message.history && message.role === "assistant" && message.runId) {
    return { source: "mixed", collapse: message.runOutcome === "completed",
      processTextIds: message.blocks.filter((block): block is TextBlock =>
        block.kind === "text" && block.phase === "commentary").map((block) => block.mid) };
  }
  // Unsuccessful runs remain expanded by default. Only known progress text and
  // tools are foldable; keep the last text and all unclassified output visible.
  if (message.role === "assistant" && message.runId
    && (message.status === "error" || message.status === "interrupted")) {
    const texts = message.blocks.filter((block): block is TextBlock => block.kind === "text" && !!block.text.trim());
    const retainedTextId = texts.at(-1)?.mid;
    return {
      source: "mixed", retainedTextId, collapse: false,
      processTextIds: texts.filter((block) => block.mid !== retainedTextId
        && block.phase === "commentary").map((block) => block.mid),
    };
  }
  // Run mode is historical capability metadata, not a presentation gate.
  const completed = message.status === "complete" && message.runOutcome === "completed";
  const streaming = message.status === "streaming" && message.runOutcome === undefined;
  if (message.role !== "assistant" || !message.runId || message.error
    || message.assistantPhase?.validation === "conflict" || (!completed && !streaming)) {
    return unavailable;
  }

  const items = new Map<string, { firstIndex: number; hasText: boolean; phase: AssistantPhase }>();
  let lastToolIndex = -1;
  let runningTool = false;
  for (const [index, block] of message.blocks.entries()) {
    if (block.kind === "tool") {
      lastToolIndex = index;
      runningTool ||= block.status === "running";
    }
    if (block.kind !== "text") continue;
    const phase = normalizeAssistantPhase(block.phase);
    if (!block.mid) return unavailable;
    const previous = items.get(block.mid);
    if (previous && previous.phase !== phase) return unavailable;
    const item = previous ?? { firstIndex: index, hasText: false, phase };
    item.hasText ||= !!block.text.trim();
    items.set(block.mid, item);
  }
  // First appearance defines logical item order, not the arrival of late fragments.
  const ordered = [...items.entries()].filter(([, item]) => item.hasText);
  const last = ordered.at(-1);
  if (!last || (completed && runningTool)) return unavailable;
  const firstFinal = ordered.findIndex(([, item]) => item.phase === "final_answer");
  if (firstFinal >= 0 && (ordered[firstFinal][1].firstIndex < lastToolIndex
    || ordered.slice(firstFinal).some(([, item]) => item.phase !== "final_answer"))) {
    return unavailable;
  }
  const hasNativePhase = ordered.some(([, item]) => item.phase !== "unknown");
  const retainedTextId = firstFinal < 0 ? last[0] : undefined;
  const hasClosingText = last[1].firstIndex > lastToolIndex;
  const collapse = completed && hasClosingText && message.assistantPhase?.validation !== "pending";
  // Keep mixed unknown text visible. Infer earlier text only for entirely phase-less
  // completed runs; explicit commentary never becomes a final answer in stored facts.
  const processTextIds = ordered.filter(([id, item]) => id !== retainedTextId
    && (item.phase === "commentary" || (!hasNativePhase && collapse))).map(([id]) => id);
  if (!hasClosingText && completed) return unavailable;
  return {
    source: firstFinal >= 0 ? "native" : hasNativePhase ? "mixed" : "inferred",
    retainedTextId,
    processTextIds,
    collapse,
  };
}

export function executionSections(
  message: ChatMessage,
  segments: MessagePresentationSegment[],
  policy = executionDisclosurePolicy(message),
  actionableToolCallIds: readonly string[] = [],
): ExecutionSection[] {
  const enabled = policy.source !== "none";
  const sections: ExecutionSection[] = [];
  segments.forEach((segment, index) => {
    const text = segment.kind === "block" && segment.block.kind === "text" ? segment.block : null;
    const processText = text && policy.processTextIds.includes(text.mid);
    // File/command approvals remain reachable in the dock. Only live social
    // login actions need their message card outside the execution disclosure.
    const execution = enabled && (processText || segment.kind === "toolActivity"
      || (segment.kind === "interactiveTool" && !actionableToolCallIds.includes(segment.block.toolCallId)));
    const previous = sections.at(-1);
    if (execution && previous?.execution) {
      previous.indexes.push(index);
      if (text) previous.textIds.push(text.mid);
    } else {
      sections.push({ id: text?.mid ?? `execution:${segment.startIndex}`,
        execution, indexes: [index], textIds: text ? [text.mid] : [] });
    }
  });
  return sections;
}
