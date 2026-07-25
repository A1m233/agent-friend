import { emit, listen, type UnlistenFn } from "@tauri-apps/api/event";

import { isTauri } from "@/utils/tauri";

const ACTIVE_SESSION_EVENT = "chat://active-session";
const ACTIVE_SESSION_REQUEST_EVENT = "chat://active-session-request";

export interface ActiveChatSessionPayload {
  sessionId: string | null;
}

async function emitEverywhere<T>(name: string, payload: T): Promise<void> {
  if (isTauri()) await emit(name, payload).catch(() => {});
  window.dispatchEvent(new CustomEvent<T>(name, { detail: payload }));
}

export async function publishActiveChatSession(sessionId: string | null): Promise<void> {
  await emitEverywhere(ACTIVE_SESSION_EVENT, { sessionId });
}

export function subscribeActiveChatSession(
  callback: (sessionId: string | null) => void,
): () => void {
  let disposed = false;
  let unlisten: UnlistenFn | null = null;
  const onWindowEvent = (event: Event) => {
    callback(
      (event as CustomEvent<ActiveChatSessionPayload>).detail?.sessionId ?? null,
    );
  };
  window.addEventListener(ACTIVE_SESSION_EVENT, onWindowEvent);
  if (isTauri()) {
    const result = listen<ActiveChatSessionPayload>(ACTIVE_SESSION_EVENT, (event) => {
      callback(event.payload.sessionId);
    });
    void Promise.resolve(result).then((next) => {
      if (typeof next !== "function") return;
      if (disposed) next();
      else unlisten = next;
    });
  }
  void emitEverywhere(ACTIVE_SESSION_REQUEST_EVENT, {});
  return () => {
    disposed = true;
    unlisten?.();
    window.removeEventListener(ACTIVE_SESSION_EVENT, onWindowEvent);
  };
}

export function subscribeActiveChatSessionRequests(
  current: () => string | null,
): () => void {
  let disposed = false;
  let unlisten: UnlistenFn | null = null;
  const publish = () => void publishActiveChatSession(current());
  window.addEventListener(ACTIVE_SESSION_REQUEST_EVENT, publish);
  if (isTauri()) {
    const result = listen(ACTIVE_SESSION_REQUEST_EVENT, publish);
    void Promise.resolve(result).then((next) => {
      if (typeof next !== "function") return;
      if (disposed) next();
      else unlisten = next;
    });
  }
  return () => {
    disposed = true;
    unlisten?.();
    window.removeEventListener(ACTIVE_SESSION_REQUEST_EVENT, publish);
  };
}
