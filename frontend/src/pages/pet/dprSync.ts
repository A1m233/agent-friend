// Page lifetime and sequence are business ordering, independent of diagnostics.
let sequence = 0;

export function startDprSync(
  readDpr: () => number,
  send: (dpr: number, version: { pageStartedAt: number; sequence: number }) => Promise<unknown>,
): () => void {
  const report = () => {
    const dpr = readDpr();
    if (!Number.isFinite(dpr) || dpr <= 0) return;
    void send(dpr, { pageStartedAt: performance.timeOrigin, sequence: ++sequence })
      .catch(() => { /* The next report retries the current value. */ });
  };
  report();
  // Recover even if a DPR-change report failed or the display observer missed it.
  const timer = setInterval(report, 2000);
  return () => clearInterval(timer);
}
