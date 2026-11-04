import { create } from "zustand";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { notificationsApi } from "@/services/api/notifications";
import type { AgentNotification, NotificationKind } from "@/types/notification";
import type { PushEnvelope } from "@/types/push";
import { isTauri } from "@/utils/tauri";

const PRIORITIES: Record<NotificationKind, number> = {
  needs_action: 0, run_failed: 1, run_interrupted: 1, todo_due: 2, reply_ready: 3, ambient: 4,
};
const RETRY_MS = 5_000;

export function selectBubbleNotifications(
  records: Record<string, AgentNotification>, limit = 4,
): AgentNotification[] {
  return Object.values(records)
    .filter((item) => item.generationStatus === "ready"
      && ["pending", "prepared", "presented"].includes(item.delivery.status))
    .sort((a, b) => (PRIORITIES[a.kind] ?? 4) - (PRIORITIES[b.kind] ?? 4)
      || a.createdAt.localeCompare(b.createdAt)
      || a.notificationId.localeCompare(b.notificationId))
    .slice(0, limit);
}

interface NotificationState {
  records: Record<string, AgentNotification>;
  expanded: Record<string, boolean>;
  ready: boolean;
  ownedAttempts: Record<string, number>;
  merge: (items: AgentNotification[]) => void;
  expand: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>((set) => ({
  records: {}, expanded: {}, ready: false, ownedAttempts: {},
  merge: (items) => set((state) => {
    let records = state.records;
    for (const item of items) {
      if (!item.notificationId || !Number.isSafeInteger(item.version)) continue;
      const previous = records[item.notificationId];
      if (previous && previous.version >= item.version) continue;
      if (records === state.records) records = { ...records };
      records[item.notificationId] = item;
    }
    return records === state.records ? state : { records };
  }),
  expand: (id) => set((state) => ({ expanded: { ...state.expanded, [id]: true } })),
}));

let generation = 0;
let refreshPromise: Promise<void> | null = null;
let windowId = "";
const preparing = new Set<string>();
const presenting = new Set<string>();
const dismissing = new Set<string>();
const retryAfter = new Map<string, number>();

export async function refreshNotifications(): Promise<void> {
  if (refreshPromise) return refreshPromise;
  const token = generation;
  const promise = (async () => {
    try {
      const snapshot = await notificationsApi.snapshot();
      if (token !== generation) return;
      useNotificationStore.getState().merge(snapshot.notifications);
      useNotificationStore.setState({ ready: true });
    } catch (error) {
      if (token === generation) useNotificationStore.setState({ ready: false });
      if (import.meta.env.DEV) console.debug("notification snapshot unavailable", error);
    }
  })();
  refreshPromise = promise;
  try { await promise; } finally { if (refreshPromise === promise) refreshPromise = null; }
}

/** Prepare only items selected for the visible stack; sound is owned by native push. */
export async function prepareNotification(id: string): Promise<void> {
  const store = useNotificationStore.getState();
  const item = store.records[id];
  if (!store.ready || !item || !["pending", "prepared"].includes(item.delivery.status)
    || (item.delivery.status === "prepared" && store.ownedAttempts[id] === item.delivery.attempt)
    || preparing.has(id) || (retryAfter.get(id) ?? 0) > Date.now()) return;
  if (!windowId) windowId = crypto.randomUUID();
  const token = generation;
  preparing.add(id);
  try {
    const result = await notificationsApi.prepare(id, item.version, windowId);
    if (token === generation) {
      useNotificationStore.getState().merge([result.notification]);
      useNotificationStore.setState((state) => ({
        ownedAttempts: { ...state.ownedAttempts, [id]: result.attempt },
      }));
    }
  } catch (error) {
    if (token !== generation) return;
    retryAfter.set(id, Date.now() + RETRY_MS);
    if (import.meta.env.DEV) console.debug("notification preparation deferred", error);
    await refreshNotifications();
  } finally {
    preparing.delete(id);
  }
}

/** Called only after DOM visibility AND a current native layout confirmation. */
export async function recordNotificationPresented(
  id: string, text: string, truncated: boolean,
): Promise<void> {
  const item = useNotificationStore.getState().records[id];
  if (!useNotificationStore.getState().ready || !item || item.delivery.status !== "prepared"
    || useNotificationStore.getState().ownedAttempts[id] !== item.delivery.attempt
    || presenting.has(id) || (retryAfter.get(`present:${id}`) ?? 0) > Date.now()) return;
  const token = generation;
  presenting.add(id);
  try {
    const result = await notificationsApi.presented(id, item.delivery.attempt, text, truncated);
    if (token !== generation) return;
    useNotificationStore.getState().merge([result.notification]);
  } catch (error) {
    if (token !== generation) return;
    retryAfter.set(`present:${id}`, Date.now() + RETRY_MS);
    if (import.meta.env.DEV) console.debug("notification presentation receipt deferred", error);
    await refreshNotifications();
  } finally {
    presenting.delete(id);
  }
}

export async function dismissNotification(id: string): Promise<void> {
  let item = useNotificationStore.getState().records[id];
  if (!item || dismissing.has(id)) return;
  dismissing.add(id);
  const token = generation;
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const result = await notificationsApi.dismiss(id, item.version);
        if (token === generation) useNotificationStore.getState().merge([result.notification]);
        return;
      } catch (error) {
        if (token !== generation) return;
        if (import.meta.env.DEV) console.debug("notification dismissal deferred", error);
        await refreshNotifications();
        const current = useNotificationStore.getState().records[id];
        if (token !== generation || !current || current.version <= item.version
          || ["dismissed", "invalidated"].includes(current.delivery.status)) return;
        // Preparation/presentation may advance the version while a close is in flight.
        // A newer active revision still carries the same user's close intent.
        item = current;
      }
    }
  } finally { dismissing.delete(id); }
}

/** Only the bubble entry starts this owner; other windows never acknowledge presentation. */
export function startNotificationSubscriber(): () => void {
  if (!isTauri()) return () => {};
  const token = ++generation;
  windowId = crypto.randomUUID();
  const unlisteners: UnlistenFn[] = [];
  let stopped = false;
  void (async () => {
    for (const [eventName, callback] of [
      ["notification://changed", (event: { payload: PushEnvelope }) => {
        if (event.payload.notification) useNotificationStore.getState().merge([event.payload.notification]);
      }],
      ["notification://push-reconnected", () => { void refreshNotifications(); }],
      ["settings://changed", () => { void refreshNotifications(); }],
    ] as const) {
      const unlisten = await listen<PushEnvelope>(eventName, (event) => {
        if (!stopped && token === generation) callback(event);
      });
      if (stopped) { unlisten(); return; }
      unlisteners.push(unlisten);
    }
    await refreshNotifications();
  })().catch((error) => console.warn("notification subscription unavailable", error));
  // Also re-evaluates unlock/foreground state and transient prepare failures.
  const timer = setInterval(() => {
    void refreshNotifications().then(() => {
      for (const item of selectBubbleNotifications(useNotificationStore.getState().records)) {
        void prepareNotification(item.notificationId);
      }
    });
  }, RETRY_MS);
  return () => {
    stopped = true;
    generation += 1;
    clearInterval(timer);
    unlisteners.forEach((unlisten) => unlisten());
    useNotificationStore.setState({ ready: false, ownedAttempts: {} });
  };
}

export function resetNotificationsForTests(): void {
  generation += 1;
  refreshPromise = null;
  windowId = "";
  preparing.clear(); presenting.clear(); dismissing.clear(); retryAfter.clear();
  useNotificationStore.setState({ records: {}, expanded: {}, ready: false, ownedAttempts: {} });
}
