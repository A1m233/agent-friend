import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";

/** Subscribe to snapshot invalidations; callers keep their reconnect/poll fallback. */
export function subscribePushInvalidation(
  events: readonly string[],
  refresh: () => void,
): () => void {
  let disposed = false;
  const subscriptions: UnlistenFn[] = [];
  if (isTauri()) {
    for (const event of events) {
      void listen(event, () => {
        if (!disposed) refresh();
      })
        .then((unlisten) => {
          if (disposed) unlisten();
          else subscriptions.push(unlisten);
        })
        .catch(() => {
          /* Snapshot polling remains available if the bridge disconnects. */
        });
    }
  }
  return () => {
    disposed = true;
    for (const unlisten of subscriptions) unlisten();
  };
}
