import type { ChatMessage } from "@/types/chat";
import type { HistoryEvent, HistoryPage } from "@/types/history";
import type { SessionDetail, SessionEvent } from "@/types/meta";
import { projectSessionEvents } from "./sessionProjection";
import { parseRunErrorRecovery } from "./taskRecovery";

export function isHistoryPage(detail: SessionDetail): detail is HistoryPage {
  return "history_items" in detail;
}

export function hasDeferredBody(event: HistoryEvent): boolean {
  return event.body.state === "deferred" || event.result_event?.body.state === "deferred";
}

/** Only complete payloads enter the legacy projection. A deferred fragment is explicit. */
export function projectHistoryEvent(page: HistoryPage, event: HistoryEvent): ChatMessage | null {
  const run = event.run;
  const events: SessionEvent[] = [];
  const complete = !hasDeferredBody(event);
  if (complete) events.push(event);
  if (event.result_event?.body.state === "inline") events.push(event.result_event);
  if (run) {
    events.push({ type: "run_terminal", uuid: `history-terminal:${event.uuid}`, ts: event.ts,
      meta: { run_id: run.run_id }, payload: { run_id: run.run_id, status: run.status,
        started_at: run.started_at, finished_at: run.finished_at } });
  }
  const projected = complete ? projectSessionEvents(events)[0] : undefined;
  if (!projected && !hasDeferredBody(event) && event.type !== "run_started") return null;
  const status = run?.status === "failed" ? "error"
    : run?.status === "completed" || event.type === "user_message" ? "complete" : "interrupted";
  const message: ChatMessage = projected ?? {
    id: event.uuid, role: event.type === "user_message" ? "user" : "assistant",
    createdAt: event.ts, blocks: [], status,
  };
  return { ...message, id: event.uuid,
    ...(run?.status === "completed" ? { runOutcome: "completed" as const } : {}),
    runId: run?.run_id ?? message.runId,
    sourceRunId: run?.source_run_id, rootRunId: run?.root_run_id,
    runStartedAt: run?.started_at, runFinishedAt: run?.finished_at,
    assistantPhase: run?.assistant_phase,
    canContinue: !!run?.can_continue && event.message_end !== false,
    ...(run?.error && event.message_end !== false ? {
      error: projected?.error ?? run.error, errorCode: projected?.errorCode ?? run.error_code,
    } : {}),
    ...(run?.error_reason || run?.recovery_action ? parseRunErrorRecovery({
      reason: run.error_reason, recovery_action: run.recovery_action,
    }, "snake") : {}),
    history: { navigationId: page.navigation_id, sessionId: page.session_id, messageId: event.message_id, version: page.content_version,
      epoch: page.history_epoch, event, first: event.message_start !== false, last: event.message_end !== false },
  };
}

/** Cache fragments individually. Paging never reprojects an already accepted event. */
export function createHistoryProjection() {
  const sessions = new Map<string, { epoch: string; entries: Map<string, { event: HistoryEvent; signature: string; message: ChatMessage | null }> }>();
  return {
    clear() { sessions.clear(); },
    prune(ids: Set<string>) { for (const id of sessions.keys()) if (!ids.has(id)) sessions.delete(id); },
    project(page: HistoryPage): ChatMessage[] {
      let cache = sessions.get(page.session_id);
      if (!cache || cache.epoch !== page.history_epoch) {
        cache = { epoch: page.history_epoch, entries: new Map() };
        sessions.set(page.session_id, cache);
      }
      const retained = new Set(page.history_items.map((event) => event.uuid));
      for (const id of cache.entries.keys()) if (!retained.has(id)) cache.entries.delete(id);
      const messages = page.history_items.flatMap((event) => {
        let entry = cache.entries.get(event.uuid);
        const signature = entry?.event === event ? entry.signature : JSON.stringify(event);
        if (!entry || entry.signature !== signature) {
          entry = { event, signature, message: projectHistoryEvent(page, event) };
          cache.entries.set(event.uuid, entry);
        } else if (entry.message?.history && (entry.message.history.version !== page.content_version
          || entry.message.history.navigationId !== page.navigation_id)) {
          entry.message = { ...entry.message, history: { ...entry.message.history,
            version: page.content_version, navigationId: page.navigation_id } };
        }
        entry.event = event;
        return entry.message ? [entry.message] : [];
      });
      const first = new Map<string, string>();
      const last = new Map<string, string>();
      for (const message of messages) {
        const id = message.history!.messageId;
        if (!first.has(id)) first.set(id, message.id);
        last.set(id, message.id);
      }
      return messages.map((message) => {
        const history = message.history!;
        const isFirst = first.get(history.messageId) === message.id;
        const isLast = last.get(history.messageId) === message.id;
        if (history.first === isFirst && history.last === isLast) return message;
        const updated = { ...message, history: { ...history, first: isFirst, last: isLast } };
        cache.entries.get(history.event.uuid)!.message = updated;
        return updated;
      });
    },
  };
}
