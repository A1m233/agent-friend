import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { INPUT_EVIDENCE_LIMITS, PetInputEvidenceCollector } from "./collector";
import type { HitObservation, HitReason, InputCorrelation } from "./types";

export type { InputCorrelation, InputCursor } from "./types";

// A page, not a React component, owns the identity and timer. StrictMode cleanup is not page exit.
let collector: PetInputEvidenceCollector | undefined;
let timer: ReturnType<typeof setInterval> | undefined;
let pageHide: (() => void) | undefined;

function createPageId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function getCollector(): PetInputEvidenceCollector | undefined {
  if (!isTauri()) return undefined;
  if (!collector) {
    collector = new PetInputEvidenceCollector({
      pageInstanceId: createPageId(),
      now: () => performance.now(),
      send: (events) => invoke("record_pet_input_evidence", { events }),
    });
    timer = setInterval(() => {
      try { collector?.flush(); } catch { /* Observation must never disturb the page. */ }
    }, INPUT_EVIDENCE_LIMITS.flushIntervalMs);
    pageHide = () => {
      try { collector?.end(); } catch { /* Unload has no guaranteed final delivery. */ }
    };
    window.addEventListener("pagehide", pageHide);
  }
  return collector;
}

function observe<T>(action: (active: PetInputEvidenceCollector) => T): T | undefined {
  try {
    const active = getCollector();
    return active ? action(active) : undefined;
  } catch {
    // Even missing browser crypto/clock or malformed diagnostic data cannot reject business IPC.
    return undefined;
  }
}

export const petInputEvidence = {
  display(actualDpr: number, width: number, height: number): void {
    observe((active) => active.observeDisplay(actualDpr, width, height));
  },
  dprReport(dpr: number): InputCorrelation | undefined {
    return observe((active) => active.dprReport(dpr, window.devicePixelRatio));
  },
  hit(observation: HitObservation): void {
    observe((active) => active.observeHit(observation, window.devicePixelRatio));
  },
  passthroughRequest(ignore: boolean, reason?: HitReason, sampleId?: number): InputCorrelation | undefined {
    return observe((active) => active.passthroughRequest(ignore, reason, sampleId));
  },
  acknowledge(kind: "dpr-ack" | "passthrough-ack", request: InputCorrelation | undefined, ok: boolean): void {
    if (request) observe((active) => active.acknowledge(kind, request, ok));
  },
};

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    if (timer !== undefined) clearInterval(timer);
    if (pageHide) window.removeEventListener("pagehide", pageHide);
    try { collector?.end(); } catch { /* Best effort only. */ }
  });
}
