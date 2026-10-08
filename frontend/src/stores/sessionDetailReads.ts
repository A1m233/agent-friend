import type { SessionDetail } from "@/types/meta";

interface PendingDetail {
  dirty: boolean;
  generation: number;
  promise: Promise<SessionDetail>;
  controller: AbortController;
  consumers: Set<symbol>;
}
const cancelled = () => new DOMException("History reader released", "AbortError");

/** Readers own subscriptions, not each other's transport; first valid pages are never withheld. */
export function createSessionDetailReads(
  load: (sessionId: string, signal: AbortSignal) => Promise<SessionDetail>,
  refreshDirty?: (sessionId: string) => void,
) {
  const pending = new Map<string, PendingDetail>();
  let generation = 0;
  return {
    read(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
      if (signal?.aborted) return Promise.reject(cancelled());
      let entry = pending.get(sessionId);
      if (!entry) {
        const controller = new AbortController();
        entry = { dirty: false, generation, controller, consumers: new Set() } as PendingDetail;
        pending.set(sessionId, entry);
        const current = entry;
        current.promise = load(sessionId, controller.signal).then((detail) => {
          if (detail.session_id !== sessionId) throw new Error("Session detail identity mismatch");
          return detail;
        }).finally(() => {
          if (pending.get(sessionId) === current) pending.delete(sessionId);
        });
        void current.promise.then(() => {
          if (current.dirty && current.generation === generation) {
            setTimeout(() => {
              if (current.generation === generation && !pending.has(sessionId)) refreshDirty?.(sessionId);
            }, 0);
          }
        }, () => undefined);
      }
      entry.generation = generation;
      const current = entry;
      const consumer = Symbol(sessionId);
      current.consumers.add(consumer);
      const release = () => {
        current.consumers.delete(consumer);
        if (!current.consumers.size && pending.get(sessionId) === current) {
          pending.delete(sessionId);
          current.controller.abort();
        }
      };
      if (!signal) {
        void current.promise.then(release, release);
        return current.promise;
      }
      return new Promise((resolve, reject) => {
        const abort = () => { release(); reject(cancelled()); };
        signal.addEventListener("abort", abort, { once: true });
        void current.promise.then(resolve, reject).finally(() => {
          signal.removeEventListener("abort", abort);
          release();
        });
      });
    },
    invalidate(sessionId: string): void {
      const entry = pending.get(sessionId);
      if (entry) entry.dirty = true;
    },
    invalidateLifecycle(): void { generation += 1; },
    reset(): void {
      for (const entry of pending.values()) entry.controller.abort();
      pending.clear();
      generation += 1;
    },
  };
}
