import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { PushEnvelope, PushEvent } from "@/types/push";
import { isTauri } from "@/utils/tauri";
import { useConversationStore } from "./conversation";
import { isSessionBusy } from "./runPresentation";
import { useCommandRuntimeStore } from "./commandRuntime";
import { useFilesystemApprovalsStore } from "./filesystemApprovals";
import { useArchivedSessionsStore, useSessionsStore } from "./sessions";
import { createStreamUpdateScheduler, type StreamUpdateScheduler } from "./streamUpdateScheduler";

const LIST_DEBOUNCE_MS = 120;
const DETAIL_DEBOUNCE_MS = 80;

let generation = 0;
let highestRevision = new Map<string, number>();
let pendingDetail = new Map<string, number>();
let listTimer: ReturnType<typeof setTimeout> | null = null;
let detailTimer: ReturnType<typeof setTimeout> | null = null;
const runEventSchedulers = new Map<string, StreamUpdateScheduler<PushEvent>>();

function runEventKey(sessionId: string, runId: string): string {
  return `${sessionId}:${runId}`;
}

function flushRunEvents(sessionId: string, runId: string): void {
  const key = runEventKey(sessionId, runId);
  const scheduler = runEventSchedulers.get(key);
  if (!scheduler) return;
  scheduler.flush();
  scheduler.dispose();
  runEventSchedulers.delete(key);
}

function disposeRunEventSchedulers(): void {
  for (const scheduler of runEventSchedulers.values()) scheduler.dispose();
  runEventSchedulers.clear();
}

function scheduleListRefresh(activeGeneration: number): void {
  if (listTimer !== null) clearTimeout(listTimer);
  listTimer = setTimeout(() => {
    listTimer = null;
    if (activeGeneration !== generation) return;
    const tasks: Promise<void>[] = [];
    if (useSessionsStore.getState().loaded) tasks.push(useSessionsStore.getState().refresh());
    if (useArchivedSessionsStore.getState().loaded) {
      tasks.push(useArchivedSessionsStore.getState().refresh());
    }
    void Promise.all(tasks);
  }, LIST_DEBOUNCE_MS);
}

function scheduleDetailRefresh(
  sessionId: string,
  revision: number | undefined,
  activeGeneration: number,
): void {
  const conversation = useConversationStore.getState();
  if (conversation.currentSessionId !== sessionId) return;
  if (isSessionBusy(conversation, sessionId)) {
    pendingDetail.set(sessionId, Math.max(pendingDetail.get(sessionId) ?? 0, revision ?? 0));
    return;
  }
  if (detailTimer !== null) clearTimeout(detailTimer);
  detailTimer = setTimeout(() => {
    detailTimer = null;
    if (activeGeneration !== generation) return;
    const state = useConversationStore.getState();
    if (state.currentSessionId !== sessionId || isSessionBusy(state, sessionId)) {
      if (state.currentSessionId === sessionId) pendingDetail.set(sessionId, revision ?? 0);
      return;
    }
    void state.refreshCurrentSession(sessionId).then(() => {
      if (activeGeneration !== generation) return;
      const latest = highestRevision.get(sessionId) ?? 0;
      if (revision !== undefined && latest > revision) {
        scheduleDetailRefresh(sessionId, latest, activeGeneration);
      }
    });
  }, DETAIL_DEBOUNCE_MS);
}

export function handleSessionChangedForSync(env: PushEnvelope, activeGeneration = generation): void {
  if (env.kind !== "session_changed" || !env.session_id) return;
  const revision = env.revision;
  if (!Number.isSafeInteger(revision) || (revision ?? 0) <= 0) return;
  const previous = highestRevision.get(env.session_id) ?? 0;
  if ((revision as number) <= previous) return;
  highestRevision.set(env.session_id, revision as number);
  if (import.meta.env.DEV) {
    console.debug("[session-sync] invalidation accepted", {
      sessionId: env.session_id,
      revision,
      currentSessionId: useConversationStore.getState().currentSessionId,
      runPhase: useConversationStore.getState().runCalibrationPhase,
    });
  }
  scheduleListRefresh(activeGeneration);
  if (useConversationStore.getState().currentSessionId === env.session_id) {
    void useConversationStore.getState().refreshApprovalMode(env.session_id);
  }
  scheduleDetailRefresh(env.session_id, revision as number, activeGeneration);
}

export function handleRunChangedForSync(env: PushEnvelope): void {
  if (env.kind !== "run_changed" || !env.run) return;
  if (env.run.status === "terminal") flushRunEvents(env.run.session_id, env.run.run_id);
  const store = useConversationStore.getState();
  store.applyRunSnapshot(env.run);
  if (env.run.status === "terminal") {
    useCommandRuntimeStore.getState().finishRun(env.run.run_id);
    useFilesystemApprovalsStore.getState().finishRun(env.run.run_id);
  }
  if (
    env.run.status === "terminal"
    && useConversationStore.getState().currentSessionId === env.run.session_id
    && !isSessionBusy(useConversationStore.getState(), env.run.session_id)
  ) {
    void useConversationStore.getState().refreshCurrentSession(env.run.session_id);
  }
}

export function handleRunEventForSync(env: PushEnvelope): void {
  if (
    env.kind !== "run_event"
    || env.presentation !== "conversation"
    || !env.session_id
    || !env.run_id
    || env.events.length === 0
  ) return;
  const key = runEventKey(env.session_id, env.run_id);
  let scheduler = runEventSchedulers.get(key);
  if (!scheduler) {
    const sessionId = env.session_id;
    const runId = env.run_id;
    scheduler = createStreamUpdateScheduler<PushEvent>((events) => {
      useConversationStore.getState().applyRunEvents(sessionId, runId, events);
    });
    runEventSchedulers.set(key, scheduler);
  }
  for (const event of env.events) scheduler.enqueue(event);
}

export function handlePushReconnectedForSync(activeGeneration = generation): void {
  disposeRunEventSchedulers();
  if (import.meta.env.DEV) {
    console.debug("[session-sync] push reconnected; scheduling calibration", {
      activeGeneration,
      generation,
      currentSessionId: useConversationStore.getState().currentSessionId,
      runPhase: useConversationStore.getState().runCalibrationPhase,
    });
  }
  scheduleListRefresh(activeGeneration);
  const calibration = useConversationStore.getState().calibrateRuns();
  const sessionId = useConversationStore.getState().currentSessionId;
  if (sessionId) {
    void useConversationStore.getState().refreshApprovalMode(sessionId);
    void calibration.then(() => {
      if (activeGeneration === generation) {
        scheduleDetailRefresh(sessionId, undefined, activeGeneration);
      }
    });
  }
}

export function startSessionSyncSubscriber(): () => void {
  generation += 1;
  const activeGeneration = generation;
  let stopped = false;
  const unlisteners: UnlistenFn[] = [];

  if (isTauri()) {
    void listen<PushEnvelope>("session://changed", (event) => {
      if (!stopped) handleSessionChangedForSync(event.payload, activeGeneration);
    }).then((unlisten) => {
      if (stopped) unlisten();
      else unlisteners.push(unlisten);
    });
    void listen("session://push-reconnected", () => {
      if (!stopped) handlePushReconnectedForSync(activeGeneration);
    }).then((unlisten) => {
      if (stopped) unlisten();
      else unlisteners.push(unlisten);
    });
    void listen<PushEnvelope>("session://run-changed", (event) => {
      if (!stopped) handleRunChangedForSync(event.payload);
    }).then((unlisten) => {
      if (stopped) unlisten();
      else unlisteners.push(unlisten);
    });
    void listen<PushEnvelope>("session://run-event", (event) => {
      if (!stopped) handleRunEventForSync(event.payload);
    }).then((unlisten) => {
      if (stopped) unlisten();
      else unlisteners.push(unlisten);
    });
  }

  const unsubscribeConversation = useConversationStore.subscribe((state, previous) => {
    const previousBusy = isSessionBusy(previous, previous.currentSessionId);
    const currentBusy = isSessionBusy(state, state.currentSessionId);
    if (previousBusy && !currentBusy && state.currentSessionId) {
      const revision = pendingDetail.get(state.currentSessionId);
      if (revision !== undefined) {
        pendingDetail.delete(state.currentSessionId);
        scheduleDetailRefresh(state.currentSessionId, revision, activeGeneration);
      }
    }
    if (state.currentSessionId !== previous.currentSessionId && previous.currentSessionId) {
      pendingDetail.delete(previous.currentSessionId);
    }
  });

  // A desktop reload can happen while a detached run is waiting for approval.
  // Push only carries future changes, so rebuild the active-run ownership map
  // from the bridge immediately; otherwise the durable tool card says
  // "waiting for approval" while ApprovalDock has no run id to match and hides
  // its buttons until another reconnect happens.
  void useConversationStore.getState().calibrateRuns();

  return () => {
    if (stopped) return;
    stopped = true;
    generation += 1;
    unlisteners.forEach((unlisten) => unlisten());
    unsubscribeConversation();
    if (listTimer !== null) clearTimeout(listTimer);
    if (detailTimer !== null) clearTimeout(detailTimer);
    listTimer = null;
    detailTimer = null;
    pendingDetail.clear();
    disposeRunEventSchedulers();
    useConversationStore.getState().invalidateBackgroundRefreshes();
  };
}

export function resetSessionSyncForTests(): void {
  generation += 1;
  highestRevision = new Map();
  pendingDetail = new Map();
  if (listTimer !== null) clearTimeout(listTimer);
  if (detailTimer !== null) clearTimeout(detailTimer);
  listTimer = null;
  detailTimer = null;
  disposeRunEventSchedulers();
}
