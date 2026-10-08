import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";

export type HistoryReadKind = "latest" | "before" | "after" | "around" | "content";
export type HistoryReadPhase = "started" | "service-wait" | "service-ready" |
  "response-received" | "decode-finished" | "projection-ready" | "state-published" |
  "react-committed" | "frame-ready" | "finished";
export type HistoryReadStatus = "success" | "error" | "cancelled";
export interface HistoryPhaseDetails {
  durationMs?: number;
  eventCount?: number;
  messageCount?: number;
  responseBytes?: number;
  requestId?: string;
}
export interface HistoryObservation {
  navigationId: string;
  phase: (phase: HistoryReadPhase, details?: HistoryPhaseDetails) => void;
  finish: (status: HistoryReadStatus) => void;
}

const active = new Map<string, HistoryObservation>();
const maximumActive = 128;

/** Counts/timing only. An unavailable native logger must never affect history. */
export function startHistoryObservation(sessionId: string, kind: HistoryReadKind): HistoryObservation {
  const navigationId = crypto.randomUUID();
  const started = performance.now();
  let finished = false;
  const emitted = new Set<HistoryReadPhase>();
  const emit = (phase: HistoryReadPhase, details: HistoryPhaseDetails = {}, status?: HistoryReadStatus) => {
    if (finished || emitted.has(phase)) return;
    emitted.add(phase);
    if (!isTauri()) return;
    // Explicit allowlist: callers cannot accidentally add prompt text or paths.
    const observation = {
      navigationId, sessionId, kind, phase, elapsedMs: performance.now() - started,
      durationMs: details.durationMs, eventCount: details.eventCount,
      messageCount: details.messageCount, responseBytes: details.responseBytes,
      requestId: details.requestId, status,
    };
    void invoke("record_history_observation", { observation }).catch(() => {
      // Missing evidence is a verification gap, never a product error.
    });
  };
  const observation: HistoryObservation = {
    navigationId,
    phase: emit,
    finish(status) {
      emit("finished", {}, status);
      finished = true;
      active.delete(navigationId);
    },
  };
  while (active.size >= maximumActive) {
    const oldest = active.values().next().value;
    oldest?.finish("cancelled");
  }
  active.set(navigationId, observation);
  emit("started");
  return observation;
}

export function recordHistoryPhase(navigationId: unknown, phase: HistoryReadPhase, details?: HistoryPhaseDetails): void {
  if (typeof navigationId === "string") active.get(navigationId)?.phase(phase, details);
}

export function finishHistoryObservation(navigationId: string | undefined, status: HistoryReadStatus): void {
  if (navigationId) active.get(navigationId)?.finish(status);
}
