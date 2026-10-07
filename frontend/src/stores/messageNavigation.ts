import type { ChatMessage } from "@/types/chat";

/** An assistant message may combine several persisted text events. */
export function findMessageAnchorIndex(
  messages: readonly ChatMessage[],
  anchorEventUuid: string,
): number {
  return messages.findIndex((message) => message.id === anchorEventUuid
    || message.blocks.some((block) => block.kind === "text" && block.mid === anchorEventUuid));
}
