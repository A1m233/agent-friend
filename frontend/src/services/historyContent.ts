import { sessionsApi } from "./api/sessions";
import type { HistoryEvent, HistoryFragment, HistoryPage } from "@/types/history";
import type { ChatMessage, MessageBlock } from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { projectHistoryEvent } from "@/stores/historyProjection";
import { finishHistoryObservation, startHistoryObservation } from "./historyObservation";

export async function readHistoryEvent(sessionId: string, version: string, event: HistoryEvent,
  signal?: AbortSignal): Promise<HistoryEvent> {
  let complete = event;
  if (event.body.state === "deferred") {
    const observation = startHistoryObservation(sessionId, "content");
    try {
      const chunks: string[] = [];
      let offset = 0;
      for (;;) {
        signal?.throwIfAborted();
        const part = await sessionsApi.historyContent(sessionId, event.body.ref, version, offset, signal, observation.navigationId);
        if (part.content_version !== version || part.offset !== offset) throw new Error("历史版本已变化");
        chunks.push(part.text);
        if (part.complete) break;
        if (part.next_offset === null || part.next_offset <= offset) throw new Error("历史内容读取未前进");
        offset = part.next_offset;
      }
      const value: unknown = JSON.parse(chunks.join(""));
      if (!value || typeof value !== "object" || !("uuid" in value) || value.uuid !== event.uuid
        || !("type" in value) || value.type !== event.type || !("ts" in value) || typeof value.ts !== "string") {
        throw new Error("历史内容身份不一致");
      }
      complete = { ...event, ...(value as SessionEvent), body: { state: "inline" } };
      observation.phase("projection-ready", { eventCount: 1, responseBytes: event.body.byte_length });
      observation.finish("success");
    } catch (error) {
      observation.finish(signal?.aborted ? "cancelled" : "error");
      throw error;
    }
  }
  if (event.result_event) {
    complete = { ...complete, result_event: await readHistoryEvent(sessionId, version, event.result_event, signal) };
  }
  return complete;
}

export function historyFragmentPage(fragment: HistoryFragment, event: HistoryEvent): HistoryPage {
  return { session_id: fragment.sessionId, title: "", persona: "", model: "",
    history_epoch: fragment.epoch, content_version: fragment.version, history_items: [event],
    before_cursor: null, after_cursor: null, has_before: false, has_after: false };
}

export function copyMessageText(message: Pick<ChatMessage, "blocks" | "status" | "error">): string {
  return message.blocks.flatMap((block: MessageBlock) => {
    if (block.kind === "text" || block.kind === "thinking") return [block.text];
    if (block.kind === "reminder") return [`提醒：${block.title}`, block.description,
      `计划时间：${block.scheduledLocal} (${block.timezone})`, `周期：${block.recurrence}`,
      `状态：${block.todoStatus}`].filter(Boolean);
    return [];
  }).concat(message.status === "error" && message.error ? [message.error] : [])
    .filter(Boolean).join("\n\n").trim();
}

/** Load the logical message's text before touching the clipboard; tools are never copied. */
export async function readHistoryCopy(fragment: HistoryFragment, signal?: AbortSignal): Promise<string> {
  const texts: string[][] = [];
  const seen = new Set<string>();
  let after: string | undefined;
  let version: string | undefined;
  for (;;) {
    const page = await sessionsApi.history(fragment.sessionId,
      { messageId: fragment.messageId, after, epoch: fragment.epoch, signal });
    version ??= page.content_version;
    if (page.content_version !== version) throw new Error("历史已更新，请重新复制");
    const pageTexts: string[] = [];
    for (const event of page.history_items) {
      if (!["user_message", "assistant_message", "todo_reminder", "run_error"].includes(event.type)) continue;
      const hydrated = await readHistoryEvent(fragment.sessionId, version, event, signal);
      const message = projectHistoryEvent(page, hydrated);
      if (message) pageTexts.push(copyMessageText(message));
    }
    finishHistoryObservation(page.navigation_id, "success");
    texts.push(pageTexts);
    if (!page.has_after || !page.after_cursor) break;
    if (seen.has(page.after_cursor)) throw new Error("历史分页未前进");
    seen.add(page.after_cursor);
    after = page.after_cursor;
  }
  signal?.throwIfAborted();
  return texts.flat().filter(Boolean).join("\n\n").trim();
}
