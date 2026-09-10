import type { SessionDetail, SessionEvent } from "@/types/meta";

/** Keep complete turn pages; a rewrite epoch invalidates all previously loaded pages. */
export function createSessionHistoryPages() {
  const entries = new Map<string, SessionDetail>();
  const events = (detail: SessionDetail): SessionEvent[] => detail.active_events ?? detail.events ?? [];
  return {
    clear() { entries.clear(); },
    prune(ids: Set<string>) {
      for (const id of entries.keys()) if (!ids.has(id)) entries.delete(id);
    },
    get(id: string) { return entries.get(id); },
    accept(detail: SessionDetail, older = false): SessionDetail {
      const previous = entries.get(detail.session_id);
      if (previous && (previous.revision ?? 0) > (detail.revision ?? 0)) return previous;
      let merged = detail;
      if (previous && detail.history_epoch !== undefined
        && previous.history_epoch === detail.history_epoch) {
        const prior = events(previous);
        const incoming = events(detail);
        if (older) {
          const seen = new Set(prior.map((event) => event.uuid));
          merged = { ...previous, active_events: [...incoming.filter((event) => !seen.has(event.uuid)), ...prior],
            history_cursor: detail.history_cursor };
        } else {
          const start = prior.findIndex((event) => event.uuid === incoming[0]?.uuid);
          if (start >= 0) merged = { ...detail, active_events: [...prior.slice(0, start), ...incoming],
            history_cursor: previous.history_cursor };
        }
      }
      entries.set(detail.session_id, merged);
      return merged;
    },
  };
}
