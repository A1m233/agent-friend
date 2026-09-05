import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { runsApi } from "@/services";
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
let highestApprovalModeRevision = new Map<string, number>();
let pendingDetail = new Map<string, number>();
let listTimer: ReturnType<typeof setTimeout> | null = null;
let detailTimer: ReturnType<typeof setTimeout> | null = null;
const runEventSchedulers = new Map<string, StreamUpdateScheduler<PushEvent>>();
interface RunEventCursor {
  runtimeInstanceId: string;
  eventSeq: number;
}

interface RuntimeReattachState {
  token: number;
  buffered: PushEnvelope[];
}

let runtimeInstanceId: string | null = null;
let runtimeReattachToken = 0;
let runEventCursors = new Map<string, RunEventCursor>();
const runtimeReattachments = new Map<string, RuntimeReattachState>();

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

function resetRuntimeFence(nextRuntimeInstanceId: string): void {
  if (runtimeInstanceId === nextRuntimeInstanceId) return;
  const replacedRuntime = runtimeInstanceId !== null;
  disposeRunEventSchedulers();
  runEventCursors.clear();
  runtimeInstanceId = nextRuntimeInstanceId;
  if (replacedRuntime) {
    useCommandRuntimeStore.getState().markDisconnected();
    useFilesystemApprovalsStore.getState().markDisconnected();
  }
}

function enqueueRunEvents(sessionId: string, runId: string, events: PushEvent[]): void {
  if (events.length === 0) return;
  const key = runEventKey(sessionId, runId);
  let scheduler = runEventSchedulers.get(key);
  if (!scheduler) {
    scheduler = createStreamUpdateScheduler<PushEvent>((items) => {
      useConversationStore.getState().applyRunEvents(sessionId, runId, items);
    });
    runEventSchedulers.set(key, scheduler);
  }
  for (const event of events) scheduler.enqueue(event);
}

function applyRunChangedEnvelope(env: PushEnvelope): void {
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

function applyRunEventEnvelope(env: PushEnvelope, allowReattach: boolean): void {
  if (
    env.kind !== "run_event"
    || env.presentation !== "conversation"
    || !env.session_id
    || !env.run_id
    || env.events.length === 0
  ) return;
  const eventRuntimeId = env.runtime_instance_id ?? null;
  const eventSeq = env.run_event_seq;
  if (eventRuntimeId && runtimeInstanceId && eventRuntimeId !== runtimeInstanceId) {
    if (allowReattach) void reattachSessionRuntimeForSync(env.session_id, generation, env);
    return;
  }
  if (eventRuntimeId && runtimeInstanceId === null) runtimeInstanceId = eventRuntimeId;
  if (eventRuntimeId && Number.isSafeInteger(eventSeq) && (eventSeq ?? 0) > 0) {
    const key = runEventKey(env.session_id, env.run_id);
    const cursor = runEventCursors.get(key);
    if (
      cursor
      && cursor.runtimeInstanceId === eventRuntimeId
      && (eventSeq as number) > cursor.eventSeq + 1
    ) {
      if (allowReattach) void reattachSessionRuntimeForSync(env.session_id, generation, env);
      return;
    }
    if (cursor?.runtimeInstanceId === eventRuntimeId && (eventSeq as number) <= cursor.eventSeq) {
      return;
    }
    runEventCursors.set(key, {
      runtimeInstanceId: eventRuntimeId,
      eventSeq: eventSeq as number,
    });
  }
  enqueueRunEvents(env.session_id, env.run_id, env.events);
}

function runtimeMatchesEnvelope(env: PushEnvelope, expectedRuntimeId: string): boolean {
  const envelopeRuntimeId = env.runtime_instance_id ?? env.run?.runtime_instance_id ?? null;
  return envelopeRuntimeId === null || envelopeRuntimeId === expectedRuntimeId;
}

export async function reattachSessionRuntimeForSync(
  sessionId: string,
  activeGeneration = generation,
  initialEnvelope?: PushEnvelope,
): Promise<void> {
  const existing = runtimeReattachments.get(sessionId);
  if (existing) {
    if (initialEnvelope) existing.buffered.push(initialEnvelope);
    return;
  }
  const token = runtimeReattachToken + 1;
  runtimeReattachToken = token;
  const state: RuntimeReattachState = {
    token,
    buffered: initialEnvelope ? [initialEnvelope] : [],
  };
  runtimeReattachments.set(sessionId, state);
  try {
    const snapshot = await runsApi.runtimeSnapshot(sessionId);
    if (
      activeGeneration !== generation
      || runtimeReattachments.get(sessionId)?.token !== token
    ) {
      if (runtimeReattachments.get(sessionId)?.token === token) {
        runtimeReattachments.delete(sessionId);
      }
      return;
    }
    const incomingRuntimeId = snapshot.runtime.bridgeInstanceId;
    resetRuntimeFence(incomingRuntimeId);
    for (const run of snapshot.runs) useConversationStore.getState().applyRunSnapshot(run);

    let journalGap = false;
    for (const run of snapshot.runs) {
      if (run.status === "terminal") continue;
      if (run.runtime_instance_id && run.runtime_instance_id !== incomingRuntimeId) continue;
      const key = runEventKey(sessionId, run.run_id);
      const cursor = runEventCursors.get(key);
      const afterSeq = cursor?.runtimeInstanceId === incomingRuntimeId ? cursor.eventSeq : 0;
      const journal = await runsApi.events(run.run_id, afterSeq);
      if (
        activeGeneration !== generation
        || runtimeReattachments.get(sessionId)?.token !== token
      ) {
        if (runtimeReattachments.get(sessionId)?.token === token) {
          runtimeReattachments.delete(sessionId);
        }
        return;
      }
      if (
        journal.runtime_instance_id !== incomingRuntimeId
        || journal.session_id !== sessionId
        || journal.run_id !== run.run_id
      ) continue;
      if (journal.gap_before_requested_seq) {
        journalGap = true;
        runEventCursors.delete(key);
        continue;
      }
      for (const record of [...journal.events].sort((left, right) => left.event_seq - right.event_seq)) {
        const current = runEventCursors.get(key);
        if (current?.runtimeInstanceId === incomingRuntimeId && record.event_seq <= current.eventSeq) {
          continue;
        }
        enqueueRunEvents(sessionId, run.run_id, [record.event]);
        runEventCursors.set(key, {
          runtimeInstanceId: incomingRuntimeId,
          eventSeq: record.event_seq,
        });
      }
    }

    const buffered = state.buffered.slice().sort((left, right) => left.seq - right.seq);
    runtimeReattachments.delete(sessionId);
    for (const envelope of buffered) {
      if (!runtimeMatchesEnvelope(envelope, incomingRuntimeId)) continue;
      if (envelope.kind === "run_changed") applyRunChangedEnvelope(envelope);
      else if (envelope.kind === "run_event") applyRunEventEnvelope(envelope, false);
    }
    await Promise.allSettled([
      useConversationStore.getState().calibrateRuns(),
      useCommandRuntimeStore.getState().refresh(),
      useFilesystemApprovalsStore.getState().refresh(),
    ]);
    if (journalGap && useConversationStore.getState().currentSessionId === sessionId) {
      await useConversationStore.getState().refreshCurrentSession(sessionId);
    }
    if (import.meta.env.DEV) {
      console.debug("[session-sync] runtime reattached", {
        sessionId,
        runtimeInstanceId: incomingRuntimeId,
        startReason: snapshot.runtime.startReason,
        resident: snapshot.sessionRuntime?.resident === true,
        journalGap,
      });
    }
  } catch (error) {
    if (runtimeReattachments.get(sessionId)?.token === token) {
      runtimeReattachments.delete(sessionId);
    }
    useCommandRuntimeStore.getState().markDisconnected();
    useFilesystemApprovalsStore.getState().markDisconnected();
    void useConversationStore.getState().calibrateRuns();
    if (useConversationStore.getState().currentSessionId === sessionId) {
      scheduleDetailRefresh(sessionId, undefined, activeGeneration);
    }
    if (import.meta.env.DEV) {
      console.warn("[session-sync] runtime reattach degraded", { sessionId, error });
    }
  }
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
  if (activeGeneration !== generation) return;
  if (env.kind !== "session_changed" || !env.session_id) return;
  const revision = env.revision;
  if (!Number.isSafeInteger(revision) || (revision ?? 0) <= 0) return;
  if (
    env.change_kind === "metadata"
    && env.origin === "approval_mode"
    && (revision as number) > (highestApprovalModeRevision.get(env.session_id) ?? 0)
  ) {
    // Revision allocation and publication are separate: newer content may
    // arrive before an older, still relevant approval-mode invalidation.
    highestApprovalModeRevision.set(env.session_id, revision as number);
    if (useConversationStore.getState().currentSessionId === env.session_id) {
      void useConversationStore.getState().refreshApprovalMode(env.session_id, { invalidate: true });
    }
  }
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
  scheduleDetailRefresh(env.session_id, revision as number, activeGeneration);
}

export function handleRunChangedForSync(env: PushEnvelope): void {
  if (env.kind !== "run_changed" || !env.run) return;
  const reattaching = runtimeReattachments.get(env.run.session_id);
  if (reattaching) {
    reattaching.buffered.push(env);
    return;
  }
  const envelopeRuntimeId = env.runtime_instance_id ?? env.run.runtime_instance_id ?? null;
  if (envelopeRuntimeId && runtimeInstanceId && envelopeRuntimeId !== runtimeInstanceId) {
    void reattachSessionRuntimeForSync(env.run.session_id, generation, env);
    return;
  }
  if (envelopeRuntimeId && runtimeInstanceId === null) runtimeInstanceId = envelopeRuntimeId;
  applyRunChangedEnvelope(env);
}

export function handleRunEventForSync(env: PushEnvelope): void {
  if (!env.session_id) return;
  const reattaching = runtimeReattachments.get(env.session_id);
  if (reattaching) {
    reattaching.buffered.push(env);
    return;
  }
  applyRunEventEnvelope(env, true);
}

export function handlePushReconnectedForSync(activeGeneration = generation): void {
  if (activeGeneration !== generation) return;
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
  const sessionId = useConversationStore.getState().currentSessionId;
  if (sessionId) {
    void useConversationStore.getState().refreshApprovalMode(sessionId, { invalidate: true });
    void reattachSessionRuntimeForSync(sessionId, activeGeneration).then(() => {
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
    if (state.currentSessionId && state.currentSessionId !== previous.currentSessionId) {
      void reattachSessionRuntimeForSync(state.currentSessionId, activeGeneration);
    }
  });

  // A desktop reload can happen while a detached run is waiting for approval.
  // Push only carries future changes, so rebuild the active-run ownership map
  // from the bridge immediately; otherwise the durable tool card says
  // "waiting for approval" while ApprovalDock has no run id to match and hides
  // its buttons until another reconnect happens.
  const initialSessionId = useConversationStore.getState().currentSessionId;
  if (initialSessionId) {
    void useConversationStore.getState().refreshApprovalMode(initialSessionId);
  }
  if (isTauri() && initialSessionId) {
    void reattachSessionRuntimeForSync(initialSessionId, activeGeneration);
  } else {
    void useConversationStore.getState().calibrateRuns();
  }

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
  highestApprovalModeRevision = new Map();
  pendingDetail = new Map();
  if (listTimer !== null) clearTimeout(listTimer);
  if (detailTimer !== null) clearTimeout(detailTimer);
  listTimer = null;
  detailTimer = null;
  disposeRunEventSchedulers();
  runtimeReattachments.clear();
  runEventCursors = new Map();
  runtimeInstanceId = null;
  runtimeReattachToken = 0;
}
