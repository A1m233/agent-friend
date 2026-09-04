import { create } from "zustand";

import {
  socialAuthApi,
  type SocialAuthRequestSnapshot,
  type SocialAuthRequestStatus,
} from "@/services";

const SOCIAL_AUTH_POLL_MS = 1_000;

const ACTIVE_STATUSES = new Set<SocialAuthRequestStatus>([
  "pending",
  "login_in_progress",
  "authenticated",
  "continuing",
]);

export type SocialAuthAction = "approve" | "decline" | "cancel";

export interface SocialAuthRequestEntry {
  snapshot: SocialAuthRequestSnapshot | null;
  loading: boolean;
  submitting: boolean;
  error: string | null;
}

interface SocialAuthRequestsState {
  entries: Record<string, SocialAuthRequestEntry>;
  trackedRequestIds: readonly string[];
  setTrackedRequestIds: (requestIds: readonly string[]) => void;
  refresh: (requestId: string) => Promise<void>;
  perform: (requestId: string, action: SocialAuthAction) => Promise<void>;
  clear: () => void;
}

const pollTimers = new Map<string, ReturnType<typeof globalThis.setTimeout>>();
const refreshInFlight = new Map<string, Promise<void>>();
const mutationVersion = new Map<string, number>();
const requestLifetimes = new Map<string, symbol>();

export function isActiveSocialAuthStatus(status: SocialAuthRequestStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

function emptyEntry(): SocialAuthRequestEntry {
  return { snapshot: null, loading: false, submitting: false, error: null };
}

function clearPoll(requestId: string): void {
  const timer = pollTimers.get(requestId);
  if (timer !== undefined) globalThis.clearTimeout(timer);
  pollTimers.delete(requestId);
}

function schedulePoll(requestId: string): void {
  clearPoll(requestId);
  const state = useSocialAuthRequestsStore.getState();
  if (!state.trackedRequestIds.includes(requestId)) return;
  const entry = state.entries[requestId];
  if (entry?.submitting) return;
  const snapshot = entry?.snapshot;
  if (snapshot && !isActiveSocialAuthStatus(snapshot.status)) return;
  pollTimers.set(requestId, globalThis.setTimeout(() => {
    pollTimers.delete(requestId);
    void useSocialAuthRequestsStore.getState().refresh(requestId);
  }, SOCIAL_AUTH_POLL_MS));
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function actionRequest(
  requestId: string,
  action: SocialAuthAction,
): Promise<SocialAuthRequestSnapshot> {
  if (action === "approve") return socialAuthApi.approve(requestId);
  if (action === "decline") return socialAuthApi.decline(requestId);
  return socialAuthApi.cancel(requestId);
}

export const useSocialAuthRequestsStore = create<SocialAuthRequestsState>((set, get) => ({
  entries: {},
  trackedRequestIds: [],
  setTrackedRequestIds(requestIds) {
    const uniqueIds = [...new Set(requestIds.filter(Boolean))];
    if (sameIds(get().trackedRequestIds, uniqueIds)) return;
    const retained = new Set(uniqueIds);
    for (const requestId of get().trackedRequestIds) {
      if (!retained.has(requestId)) {
        clearPoll(requestId);
        requestLifetimes.delete(requestId);
        refreshInFlight.delete(requestId);
        mutationVersion.delete(requestId);
      }
    }
    const addedIds = uniqueIds.filter((id) => !get().trackedRequestIds.includes(id));
    for (const id of addedIds) requestLifetimes.set(id, Symbol(id));
    set((state) => ({
      trackedRequestIds: uniqueIds,
      entries: Object.fromEntries(uniqueIds.map((requestId) => [
        requestId,
        state.entries[requestId] ?? emptyEntry(),
      ])),
    }));
    for (const requestId of addedIds) void get().refresh(requestId);
  },
  async refresh(requestId) {
    const existing = refreshInFlight.get(requestId);
    if (existing) return existing;
    if (!get().trackedRequestIds.includes(requestId)) return;
    const requestVersion = mutationVersion.get(requestId) ?? 0;
    const lifetime = requestLifetimes.get(requestId);
    const task = (async () => {
      set((state) => ({
        entries: {
          ...state.entries,
          [requestId]: {
            ...(state.entries[requestId] ?? emptyEntry()),
            loading: true,
            error: null,
          },
        },
      }));
      try {
        const snapshot = await socialAuthApi.get(requestId);
        if (
          !get().trackedRequestIds.includes(requestId)
          || requestLifetimes.get(requestId) !== lifetime
          || (mutationVersion.get(requestId) ?? 0) !== requestVersion
        ) return;
        set((state) => ({
          entries: {
            ...state.entries,
            [requestId]: {
              ...(state.entries[requestId] ?? emptyEntry()),
              snapshot,
              loading: false,
              error: null,
            },
          },
        }));
      } catch {
        if (
          !get().trackedRequestIds.includes(requestId)
          || requestLifetimes.get(requestId) !== lifetime
          || (mutationVersion.get(requestId) ?? 0) !== requestVersion
        ) return;
        set((state) => ({
          entries: {
            ...state.entries,
            [requestId]: {
              ...(state.entries[requestId] ?? emptyEntry()),
              loading: false,
              error: "暂时无法读取登录状态，请稍后再试。",
            },
          },
        }));
      } finally {
        if (requestLifetimes.get(requestId) === lifetime) {
          refreshInFlight.delete(requestId);
          schedulePoll(requestId);
        }
      }
    })();
    refreshInFlight.set(requestId, task);
    return task;
  },
  async perform(requestId, action) {
    if (!get().trackedRequestIds.includes(requestId)) return;
    if (get().entries[requestId]?.submitting) return;
    const lifetime = requestLifetimes.get(requestId);
    mutationVersion.set(requestId, (mutationVersion.get(requestId) ?? 0) + 1);
    clearPoll(requestId);
    set((state) => ({
      entries: {
        ...state.entries,
        [requestId]: {
          ...(state.entries[requestId] ?? emptyEntry()),
          loading: false,
          submitting: true,
          error: null,
        },
      },
    }));
    try {
      const snapshot = await actionRequest(requestId, action);
      if (!get().trackedRequestIds.includes(requestId) || requestLifetimes.get(requestId) !== lifetime) return;
      set((state) => ({
        entries: {
          ...state.entries,
          [requestId]: {
            ...(state.entries[requestId] ?? emptyEntry()),
            snapshot,
            submitting: false,
            error: null,
          },
        },
      }));
    } catch {
      if (!get().trackedRequestIds.includes(requestId) || requestLifetimes.get(requestId) !== lifetime) return;
      set((state) => ({
        entries: {
          ...state.entries,
          [requestId]: {
            ...(state.entries[requestId] ?? emptyEntry()),
            submitting: false,
            error: "这次操作没有成功，请刷新状态后再试。",
          },
        },
      }));
    } finally {
      if (requestLifetimes.get(requestId) === lifetime) schedulePoll(requestId);
    }
  },
  clear() {
    for (const requestId of pollTimers.keys()) clearPoll(requestId);
    mutationVersion.clear();
    requestLifetimes.clear();
    refreshInFlight.clear();
    set({ entries: {}, trackedRequestIds: [] });
  },
}));
