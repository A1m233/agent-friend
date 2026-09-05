// Investigation-only: opt-in dev startup evidence, without logging or extra IPC.
// Remove after the pet DPR desynchronization investigation is complete.
type DiagnosticDetails = Record<string, string | number | boolean | null>;

interface PetDprDiagnosticEntry {
  seq: number;
  timestampMs: number;
  elapsedMs: number;
  phase: string;
  actualDpr: number;
  width: number;
  height: number;
  details: DiagnosticDetails;
}

interface PetDprDiagnosticTrace {
  version: 1;
  startedAt: number;
  entries: PetDprDiagnosticEntry[];
}

declare global {
  interface Window {
    __AGENT_FRIEND_PET_DPR_TRACE__?: PetDprDiagnosticTrace;
  }
}

const enabled = import.meta.env.DEV && import.meta.env.VITE_PET_DPR_TRACE === "1";
const startedAt = enabled ? Date.now() : 0;
const startedMonotonic = enabled ? performance.now() : 0;
const trace: PetDprDiagnosticTrace = { version: 1, startedAt, entries: [] };

if (enabled && typeof window !== "undefined") {
  Object.defineProperty(window, "__AGENT_FRIEND_PET_DPR_TRACE__", {
    value: trace,
    configurable: true,
  });
}

export function recordPetDprDiagnostic(
  phase: string,
  details: DiagnosticDetails = {},
): number | undefined {
  if (!enabled || typeof window === "undefined" || trace.entries.length >= 100) return;
  const elapsedMs = performance.now() - startedMonotonic;
  if (elapsedMs > 2_000) return;
  const seq = trace.entries.length + 1;
  trace.entries.push({
    seq,
    timestampMs: Date.now(),
    elapsedMs,
    phase,
    actualDpr: window.devicePixelRatio,
    width: window.innerWidth,
    height: window.innerHeight,
    details: { ...details },
  });
  return seq;
}
