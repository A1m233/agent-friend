import type { SessionDetail, SessionEvent } from "@/types/meta";
import { isHistoryPage } from "./historyProjection";
import type { HistoryEvent, HistoryPage } from "@/types/history";

const MAX_HISTORY_ITEMS = 512;
const MAX_HISTORY_BYTES = 4 * 1024 * 1024;
const sizes = new WeakMap<HistoryEvent, number>();
function boundedPage(page: HistoryPage, keepStart: boolean): HistoryPage {
  const items = keepStart ? page.history_items : [...page.history_items].reverse();
  const retained: HistoryEvent[] = [];
  let used = 0;
  for (const item of items) {
    const size = sizes.get(item) ?? new TextEncoder().encode(JSON.stringify(item)).length;
    sizes.set(item, size);
    if (retained.length >= MAX_HISTORY_ITEMS || used + size > MAX_HISTORY_BYTES) break;
    retained.push(item); used += size;
  }
  if (retained.length === items.length) return page;
  if (!keepStart) retained.reverse();
  return { ...page, history_items: retained,
    ...(keepStart ? { after_cursor: retained.at(-1)?.uuid ?? null, has_after: true } :
      { before_cursor: retained[0]?.uuid ?? null, history_cursor: retained[0]?.uuid ?? null, has_before: true }) };
}

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
    replace(detail: SessionDetail) { entries.set(detail.session_id, detail); return detail; },
    accept(detail: SessionDetail, older = false): SessionDetail {
      const previous = entries.get(detail.session_id);
      if (previous && (previous.revision ?? 0) > (detail.revision ?? 0)) return previous;
      let merged = detail;
      if (isHistoryPage(detail)) {
        if (previous && isHistoryPage(previous) && previous.history_epoch === detail.history_epoch) {
          const incoming = new Map(detail.history_items.map((event) => [event.uuid, event]));
          const runs = new Map(detail.history_items.filter((event) => event.run)
            .map((event) => [event.run!.run_id, event.run!]));
          const prior = previous.history_items.filter((event) => !incoming.has(event.uuid)).map((event) => {
            const run = event.run && runs.get(event.run.run_id);
            return run && JSON.stringify(run) !== JSON.stringify(event.run) ? { ...event, run } : event;
          });
          merged = { ...detail,
            history_items: older ? [...detail.history_items, ...prior] : [...prior, ...detail.history_items],
            ...(older ? { after_cursor: previous.after_cursor, has_after: previous.has_after } :
              { before_cursor: previous.before_cursor, history_cursor: previous.history_cursor, has_before: previous.has_before }),
          };
        }
        if (isHistoryPage(merged)) merged = boundedPage(merged, older);
        entries.set(detail.session_id, merged);
        return merged;
      }
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
