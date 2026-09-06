import type { CancelRunResponse, RunSnapshot } from "@/types/run";

export type StopRequestPhase = "requesting" | "accepted";
interface CancellationIntent {
  runtimeId: string | null;
  sessionId: string;
  runId: string;
  phase: StopRequestPhase;
}

const STORAGE_KEY = "agent-friend:pending-run-cancellations:v1";

function windowStorage(): Storage | undefined {
  try { return typeof window === "undefined" ? undefined : window.sessionStorage; }
  catch { return undefined; }
}

export function createRunCancellation(options: {
  cancel: (sessionId: string, runId: string, runtimeId?: string) => Promise<CancelRunResponse>;
  snapshot: (snapshot: RunSnapshot) => void;
  accepted: (sessionId: string, runId: string) => void;
  changed: (phases: Record<string, StopRequestPhase>) => void;
  runtimeMismatch?: () => void;
  storage?: () => Storage | undefined;
}) {
  const intents = new Map<string, CancellationIntent>();
  const flights = new Map<string, Promise<"accepted" | "pending">>();
  const observedVersions = new Map<string, number>();
  const retryAfterFlight = new Set<string>();
  let runtimeId: string | null = null;
  let restored = false;
  const storage = options.storage ?? windowStorage;

  function publish() {
    options.changed(Object.fromEntries([...intents].map(([id, intent]) => [id, intent.phase])));
    try {
      if (intents.size) storage()?.setItem(STORAGE_KEY, JSON.stringify([...intents.values()]));
      else storage()?.removeItem(STORAGE_KEY);
    } catch { /* Window recovery is best effort; live intent remains authoritative. */ }
  }

  function forget(runId: string) {
    if (!intents.delete(runId)) return;
    observedVersions.delete(runId);
    retryAfterFlight.delete(runId);
    publish();
  }

  function attempt(intent: CancellationIntent): Promise<"accepted" | "pending"> {
    const pending = flights.get(intent.runId);
    if (pending) return pending;
    if (intents.get(intent.runId) !== intent || intent.phase === "accepted") {
      return Promise.resolve("accepted");
    }
    const flight = (async (): Promise<"accepted" | "pending"> => {
      try {
        const response = await options.cancel(intent.sessionId, intent.runId, intent.runtimeId ?? undefined);
        if (intents.get(intent.runId) !== intent) return "accepted";
        const run = response.run;
        if (
          run.run_id !== intent.runId || run.session_id !== intent.sessionId
          || (intent.runtimeId && run.runtime_instance_id && intent.runtimeId !== run.runtime_instance_id)
        ) return "pending";
        intent.phase = "accepted";
        publish();
        options.snapshot(run);
        options.accepted(intent.sessionId, intent.runId);
        if (run.status === "terminal") forget(intent.runId);
        return "accepted";
      } catch (error) {
        if (intents.get(intent.runId) !== intent) return "pending";
        if (error && typeof error === "object" && "code" in error && error.code === "run_runtime_mismatch") {
          forget(intent.runId);
          options.runtimeMismatch?.();
        }
        // 404 and transport failures are not rejection. A later lifecycle signal retries.
        return "pending";
      }
    })().finally(() => {
      if (flights.get(intent.runId) !== flight) return;
      flights.delete(intent.runId);
      if (retryAfterFlight.delete(intent.runId) && intents.get(intent.runId) === intent) {
        void attempt(intent);
      }
    });
    flights.set(intent.runId, flight);
    return flight;
  }

  function signalRetry(intent: CancellationIntent) {
    if (intent.phase === "accepted") return Promise.resolve("accepted" as const);
    if (flights.has(intent.runId)) retryAfterFlight.add(intent.runId);
    return attempt(intent);
  }

  return {
    restore() {
      if (restored) return;
      restored = true;
      try {
        const parsed: unknown = JSON.parse(storage()?.getItem(STORAGE_KEY) ?? "[]");
        if (Array.isArray(parsed)) for (const value of parsed) {
          if (!value || typeof value !== "object") continue;
          const row = value as Partial<CancellationIntent>;
          if (
            typeof row.runId !== "string" || !row.runId || typeof row.sessionId !== "string" || !row.sessionId
            || (row.runtimeId !== null && typeof row.runtimeId !== "string")
            || (row.phase !== "requesting" && row.phase !== "accepted")
          ) continue;
          // Reconstruct the whitelist, never retain unknown fields from storage.
          if (intents.has(row.runId)) continue;
          intents.set(row.runId, {
            runId: row.runId, sessionId: row.sessionId, runtimeId: row.runtimeId,
            phase: row.phase,
          });
        }
      } catch { /* Ignore corrupt or unavailable session storage. */ }
      publish();
    },
    request(run: RunSnapshot) {
      const existing = intents.get(run.run_id);
      if (existing) return attempt(existing);
      const intent: CancellationIntent = {
        runId: run.run_id, sessionId: run.session_id,
        runtimeId: run.runtime_instance_id ?? runtimeId, phase: "requesting",
      };
      intents.set(run.run_id, intent);
      publish();
      return attempt(intent);
    },
    observe(run: RunSnapshot) {
      const intent = intents.get(run.run_id);
      if (!intent || intent.sessionId !== run.session_id) return;
      if (intent.runtimeId && run.runtime_instance_id && intent.runtimeId !== run.runtime_instance_id) return;
      if (run.runtime_instance_id && intent.runtimeId === null) {
        intent.runtimeId = run.runtime_instance_id;
        publish();
      }
      if (run.status === "terminal") { forget(run.run_id); return; }
      if (run.version <= (observedVersions.get(run.run_id) ?? -1)) return;
      observedVersions.set(run.run_id, run.version);
      void signalRetry(intent);
    },
    setRuntime(next: string) {
      runtimeId = next;
      for (const intent of intents.values()) {
        if (intent.runtimeId && intent.runtimeId !== next) forget(intent.runId);
      }
    },
    retry(runId?: string) {
      return Promise.all([...intents.values()]
        .filter((intent) => !runId || intent.runId === runId)
        .map(attempt));
    },
    has(runId: string) { return intents.has(runId); },
    identities() { return [...intents.values()].map(({ runId, sessionId, runtimeId }) => ({ runId, sessionId, runtimeId })); },
    forget,
    reset() {
      intents.clear(); flights.clear(); observedVersions.clear(); retryAfterFlight.clear();
      runtimeId = null; restored = false;
    },
  };
}
