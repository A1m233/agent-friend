import type { ChatMessage } from "@/types/chat";
import type { PushEvent } from "@/types/push";
import { applyPushConversationEvent } from "./conversationReducer";
import { projectHistoryEvent } from "./historyProjection";
import { historyFragmentPage } from "@/services/historyContent";

/** Keep the live reducer intact, routing correlated updates to their historical fragment. */
export function applyHistoryRunEvents(input: ChatMessage[], runId: string, events: PushEvent[]): ChatMessage[] | null {
  if (!input.some((message) => message.runId === runId && message.history)) return null;
  let messages = [...input];
  for (const event of events) {
    if (event.type === "assistant_message_reset" && Array.isArray(event.message_ids)) {
      const removed = new Set(event.message_ids);
      messages = messages.filter((message) => message.runId !== runId || !message.history
        || !removed.has(message.history.event.uuid));
    }
    if (["done", "assistant_phase"].includes(event.type)) {
      messages = messages.map((message) => message.runId === runId
        ? applyPushConversationEvent(message, event, runId) : message);
      continue;
    }
    const toolId = event.event_tool_call_id ?? event.tool_call_id;
    const textId = event.message_id;
    let index = messages.findIndex((message) => message.runId === runId && (
      (typeof textId === "string" && (message.history?.event.uuid === textId
        || message.blocks.some((block) => block.kind === "text" && block.mid === textId)))
      || (typeof toolId === "string" && (message.blocks.some((block) => block.kind === "tool" && block.toolCallId === toolId)
        || (message.history?.event.type === "tool_call_request"
          && (message.history.event.payload?.event_tool_call_id ?? message.history.event.payload?.tool_call_id) === toolId)))
    ));
    if (index >= 0 && event.type === "tool_call_request") continue;
    if (index >= 0 && event.type === "tool_call_result" && messages[index].history) {
      const old = messages[index];
      const fragment = old.history!;
      const request = { ...fragment.event, result_event: {
        uuid: fragment.event.result_event?.uuid ?? `${fragment.event.uuid}:live-result`,
        type: "tool_call_result", ts: new Date().toISOString(), message_id: fragment.messageId,
        meta: { run_id: runId }, body: { state: "inline" as const },
        payload: { ...event, content: event.text },
      } };
      const projected = projectHistoryEvent(historyFragmentPage(fragment, request), request);
      if (projected) messages[index] = projected;
      continue;
    }
    if (index < 0) {
      index = messages.findIndex((message) => message.runId === runId && !message.history);
      if (index < 0) {
        index = messages.length;
        messages.push({ id: `live:${runId}`, runId, role: "assistant", status: "streaming",
          createdAt: new Date().toISOString(), blocks: [] });
      }
    }
    messages[index] = applyPushConversationEvent(messages[index], event, runId);
  }
  return messages;
}
