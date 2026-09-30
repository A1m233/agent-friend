import { BRIDGE_BASE_URL } from "@/constants";
import { ensureLocalServiceReady } from "@/services/runtime";

/** A connection owns the current view's pin; every window has an independent pin. */
export function retainSessionUsage(sessionId: string | null): () => void {
  let disposed = false;
  let source: EventSource | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let delay = 1000;
  if (sessionId !== null) {
    const connect = () => {
      void ensureLocalServiceReady("bridge").then(() => {
        if (disposed) return;
        source = new EventSource(
          `${BRIDGE_BASE_URL}/v1/sessions/${encodeURIComponent(sessionId)}/usage`,
        );
      }).catch(() => {
        if (!disposed) {
          retry = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 10_000);
        }
      });
    };
    connect();
  }
  return () => {
    disposed = true;
    clearTimeout(retry);
    source?.close();
  };
}
