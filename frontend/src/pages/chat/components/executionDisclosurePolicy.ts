import type { ChatMessage } from "@/types/chat";
import { canDiscloseExecution } from "@/stores/assistantPhase";
import { toolPresentation, type MessagePresentationSegment } from "./toolActivityPresentation";

export interface ExecutionSection {
  id: string;
  execution: boolean;
  indexes: number[];
  textIds: string[];
}

export function executionSections(message: ChatMessage, segments: MessagePresentationSegment[]): ExecutionSection[] {
  const enabled = canDiscloseExecution(message);
  const sections: ExecutionSection[] = [];
  segments.forEach((segment, index) => {
    const text = segment.kind === "block" && segment.block.kind === "text" ? segment.block : null;
    const execution = enabled && (text?.phase === "commentary"
      || (segment.kind === "toolActivity" && segment.blocks.every((block) =>
        !block.name.startsWith("request_") && !["warning", "error"].includes(toolPresentation(block).status))));
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
