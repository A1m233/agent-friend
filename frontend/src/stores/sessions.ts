/**
 * 历史会话列表 store（design §4.3/§4.4）。store = 本窗口视图缓存，bridge 为真相源。
 */

import { create } from "zustand";
import { emit, listen } from "@tauri-apps/api/event";
import { sessionsApi } from "@/services";
import type { SessionSummary } from "@/types/meta";
import { isTauri } from "@/utils/tauri";

const SESSIONS_CHANGED_EVENT = "sessions://changed";
const sessionsEventOrigin = `sessions-${Math.random().toString(36).slice(2)}`;

interface SessionsChangedPayload {
  action?: string;
  sessionId?: string;
  originId?: string;
  [key: string]: unknown;
}

interface SessionsState {
  list: SessionSummary[];
  loading: boolean;
  /** 至少成功拉取过一次列表；用于区分“空列表”和“还没拿到真相源”。 */
  loaded: boolean;
  /** 从 bridge 重新拉取会话列表（新建/切换后调用刷新）。 */
  refresh: () => Promise<void>;
  rename: (sessionId: string, title: string) => Promise<boolean>;
  archive: (sessionId: string) => Promise<boolean>;
}

let refreshSeq = 0;

interface ArchivedSessionsState {
  list: SessionSummary[];
  query: string;
  loading: boolean;
  loaded: boolean;
  pendingIds: string[];
  deletingAll: boolean;
  setQuery: (query: string) => void;
  refresh: () => Promise<void>;
  unarchive: (sessionId: string) => Promise<boolean>;
  remove: (sessionId: string) => Promise<boolean>;
  removeAll: () => Promise<boolean>;
}

let archivedRefreshSeq = 0;
let sessionsChangedSubscriberStarted = false;

export const useSessionsStore = create<SessionsState>((set) => ({
  list: [],
  loading: false,
  loaded: false,
  async refresh() {
    const seq = refreshSeq + 1;
    refreshSeq = seq;
    set({ loading: true });
    try {
      const list = await sessionsApi.list();
      if (seq === refreshSeq) {
        set({ list, loading: false, loaded: true });
      }
    } catch {
      // 列表拉取失败不弹技术错误，保留旧列表即可。
      if (seq === refreshSeq) {
        set({ loading: false });
      }
    }
  },
  async rename(sessionId, title) {
    try {
      await sessionsApi.rename(sessionId, title);
      await emitSessionsChanged({ sessionId, action: "rename" });
      await useSessionsStore.getState().refresh();
      return true;
    } catch {
      return false;
    }
  },
  async archive(sessionId) {
    try {
      await sessionsApi.archive(sessionId);
      await emitSessionsChanged({ sessionId, action: "archive" });
      await useSessionsStore.getState().refresh();
      return true;
    } catch {
      return false;
    }
  },
}));

export const useArchivedSessionsStore = create<ArchivedSessionsState>((set, get) => ({
  list: [],
  query: "",
  loading: false,
  loaded: false,
  pendingIds: [],
  deletingAll: false,
  setQuery(query) {
    set({ query });
  },
  async refresh() {
    const seq = archivedRefreshSeq + 1;
    archivedRefreshSeq = seq;
    const query = get().query;
    set({ loading: true });
    try {
      const list = await sessionsApi.list({ archived: true, q: query });
      if (seq === archivedRefreshSeq) {
        set({ list, loading: false, loaded: true });
      }
    } catch {
      if (seq === archivedRefreshSeq) {
        set({ loading: false });
      }
    }
  },
  async unarchive(sessionId) {
    setPending(sessionId, true);
    try {
      await sessionsApi.unarchive(sessionId);
      await emitSessionsChanged({ sessionId, action: "unarchive" });
      await Promise.all([
        useArchivedSessionsStore.getState().refresh(),
        useSessionsStore.getState().refresh(),
      ]);
      return true;
    } catch {
      return false;
    } finally {
      setPending(sessionId, false);
    }
  },
  async remove(sessionId) {
    setPending(sessionId, true);
    try {
      await sessionsApi.delete(sessionId);
      await emitSessionsChanged({ sessionId, action: "delete" });
      await Promise.all([
        useArchivedSessionsStore.getState().refresh(),
        useSessionsStore.getState().refresh(),
      ]);
      return true;
    } catch {
      return false;
    } finally {
      setPending(sessionId, false);
    }
  },
  async removeAll() {
    set({ deletingAll: true });
    try {
      await sessionsApi.deleteArchived();
      await emitSessionsChanged({ action: "delete-archived" });
      await Promise.all([
        useArchivedSessionsStore.getState().refresh(),
        useSessionsStore.getState().refresh(),
      ]);
      return true;
    } catch {
      return false;
    } finally {
      set({ deletingAll: false });
    }
  },
}));

function setPending(sessionId: string, pending: boolean): void {
  useArchivedSessionsStore.setState((state) => {
    const pendingIds = new Set(state.pendingIds);
    if (pending) {
      pendingIds.add(sessionId);
    } else {
      pendingIds.delete(sessionId);
    }
    return { pendingIds: Array.from(pendingIds) };
  });
}

function invalidateLoadedSessionStores(): void {
  const tasks: Array<Promise<void>> = [];
  if (useSessionsStore.getState().loaded) {
    tasks.push(useSessionsStore.getState().refresh());
  }
  if (useArchivedSessionsStore.getState().loaded) {
    tasks.push(useArchivedSessionsStore.getState().refresh());
  }
  void Promise.all(tasks);
}

function handleSessionsChanged(payload: SessionsChangedPayload | null | undefined): void {
  if (payload?.originId === sessionsEventOrigin) return;
  invalidateLoadedSessionStores();
}

export function startSessionsChangedSubscriber(): void {
  if (sessionsChangedSubscriberStarted) return;
  sessionsChangedSubscriberStarted = true;

  if (isTauri()) {
    void listen<SessionsChangedPayload>(SESSIONS_CHANGED_EVENT, (event) => {
      handleSessionsChanged(event.payload);
    });
  }

  if (typeof window !== "undefined") {
    window.addEventListener(SESSIONS_CHANGED_EVENT, (event) => {
      handleSessionsChanged((event as CustomEvent<SessionsChangedPayload>).detail);
    });
  }
}

export async function emitSessionsChanged(
  payload: Omit<SessionsChangedPayload, "originId">,
): Promise<void> {
  const eventPayload = { ...payload, originId: sessionsEventOrigin };
  if (isTauri()) {
    await emit(SESSIONS_CHANGED_EVENT, eventPayload).catch(() => {});
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(SESSIONS_CHANGED_EVENT, { detail: eventPayload }));
  }
}
