import type { SessionDetail } from "@/types/meta";

interface PendingDetail {
  dirty: boolean;
  generation: number;
  promise: Promise<SessionDetail>;
}

/** Share reads with one trailing read per batch; dirty batches make progress, then refresh again. */
export function createSessionDetailReads(
  load: (sessionId: string) => Promise<SessionDetail>,
  refreshDirty?: (sessionId: string) => void,
) {
  const pending = new Map<string, PendingDetail>();
  let generation = 0;
  return {
    read(sessionId: string): Promise<SessionDetail> {
      const existing = pending.get(sessionId);
      if (existing) {
        if (existing.generation !== generation) existing.dirty = true;
        existing.generation = generation;
        return existing.promise;
      }
      const entry = { dirty: false, generation } as PendingDetail;
      pending.set(sessionId, entry);
      entry.promise = (async () => {
        try {
          let detail: SessionDetail;
          let reads = 0;
          do {
            entry.dirty = false;
            detail = await load(sessionId);
            if (detail.session_id !== sessionId) throw new Error("Session detail identity mismatch");
            reads += 1;
          } while (entry.dirty && reads < 2 && pending.get(sessionId) === entry);
          return detail;
        } finally {
          if (pending.get(sessionId) === entry) pending.delete(sessionId);
        }
      })();
      void entry.promise.then(() => {
        if (!entry.dirty) return;
        // Resolve current readers before scheduling another dirty-driven batch.
        // Continuous updates must not starve the first history render.
        queueMicrotask(() => {
          if (entry.generation === generation && !pending.has(sessionId)) refreshDirty?.(sessionId);
        });
      }, () => undefined);
      return entry.promise;
    },
    invalidate(sessionId: string): void {
      const entry = pending.get(sessionId);
      if (entry) entry.dirty = true;
    },
    invalidateLifecycle(): void {
      generation += 1;
    },
    reset(): void {
      pending.clear();
      generation += 1;
    },
  };
}
