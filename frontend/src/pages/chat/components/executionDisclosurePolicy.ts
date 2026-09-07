import type { ChatMessage } from "@/types/chat";
import { canDiscloseExecution, shouldCollapseExecution } from "@/stores/assistantPhase";
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
  source: "native" | "inferred" | "none";
  finalTextId?: string;
  collapse: boolean;
}

export function executionDisclosurePolicy(message: ChatMessage): ExecutionDisclosurePolicy {
  if (message.role !== "assistant") return { source: "none", collapse: false };
  if (canDiscloseExecution(message)) {
    return { source: "native", collapse: shouldCollapseExecution(message) };
  }
  // Missing/invalid native semantics must not silently become a successful inference.
  if (message.assistantPhase?.mode === "native" || message.assistantPhase?.validation === "conflict"
    || !message.runId || message.status !== "complete" || message.runOutcome !== "completed"
    || message.error || message.blocks.some((block) => block.kind === "tool" && block.status === "running")) {
    return { source: "none", collapse: false };
  }

  const items = new Map<string, { firstIndex: number; hasText: boolean }>();
  for (const [index, block] of message.blocks.entries()) {
    if (block.kind !== "text") continue;
    if (!block.mid || (block.phase !== undefined && block.phase !== "unknown")) {
      return { source: "none", collapse: false };
    }
    const item = items.get(block.mid) ?? { firstIndex: index, hasText: false };
    item.hasText ||= !!block.text.trim();
    items.set(block.mid, item);
  }
  // Map insertion order is logical item order; a late fragment of an earlier item
  // cannot become the final answer just because its fragment is last in the array.
  const finalItem = [...items.entries()].reverse().find(([, item]) => item.hasText);
  if (!finalItem || message.blocks.slice(finalItem[1].firstIndex + 1).some((block) => block.kind === "tool")) {
    return { source: "none", collapse: false };
  }
  return { source: "inferred", finalTextId: finalItem[0], collapse: true };
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
    const processText = text && (policy.source === "native"
      ? text.phase === "commentary"
      : policy.source === "inferred" && text.mid !== policy.finalTextId);
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
