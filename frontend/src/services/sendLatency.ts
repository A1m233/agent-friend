/** Client-local preparation timings; never compare this clock to the backend clock. */
export function startSendPhase(
  runId: string,
  phase: "service_ready" | "request_encode" | "http_response",
): (status: "ok" | "error") => void {
  const started = performance.now();
  return (status) => {
    try {
      console.info("[send-latency]", {
        runId, phase, status, durationMs: performance.now() - started,
      });
    } catch {
      // Observability must not change admission, cancellation, or transport outcomes.
    }
  };
}
