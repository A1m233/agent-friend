import { finishHistoryObservation } from "@/services/historyObservation";

let controller = new AbortController();
const accepted = new Map<string, string>();
const pending = new Map<string, AbortSignal | undefined>();
const watched = new WeakSet<AbortSignal>();

export function trackHistoryRead(id: string, signal?: AbortSignal): void {
  if (signal?.aborted) { finishHistoryObservation(id, "cancelled"); return; }
  pending.set(id, signal);
  while (pending.size > 128) finishHistoryRead(pending.keys().next().value!, "cancelled");
  if (!signal || watched.has(signal)) return;
  watched.add(signal);
  signal.addEventListener("abort", () => {
    for (const [navigationId, owner] of pending) if (owner === signal) finishHistoryRead(navigationId, "cancelled");
  }, { once: true });
}
export function finishHistoryRead(id: string | undefined, status: "success" | "error" | "cancelled"): void {
  if (!id) return;
  pending.delete(id);
  finishHistoryObservation(id, status);
}
export function acceptHistoryNavigation(sessionId: string, id?: string): void {
  if (!id) return;
  accepted.delete(sessionId);
  accepted.set(sessionId, id);
  if (accepted.size > 24) accepted.delete(accepted.keys().next().value!);
}
export function historyNavigation(sessionId: string | null): string | undefined {
  return sessionId ? accepted.get(sessionId) : undefined;
}

export function historySignal(): AbortSignal { return controller.signal; }
export function renewHistoryView(): AbortSignal {
  controller.abort();
  controller = new AbortController();
  return controller.signal;
}
export function releaseHistoryView(): void { controller.abort(); }
