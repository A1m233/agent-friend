import type { BaseEvent } from "@ag-ui/core";

export const DEFAULT_STREAM_FLUSH_MS = 33;

interface StreamUpdateSchedulerOptions {
  flushDelayMs?: number;
  setTimer?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void;
}

export interface StreamUpdateScheduler<TEvent = BaseEvent> {
  enqueue: (event: TEvent) => void;
  flush: () => void;
  dispose: () => void;
}

/**
 * Coalesces high-frequency AG-UI stream events before they hit the React store.
 *
 * The stream can deliver many small text deltas in one answer. Flushing every
 * event forces the whole chat surface to re-render and re-parse markdown for
 * the growing answer. This scheduler keeps the stream responsive while capping
 * UI update frequency to a predictable cadence.
 */
export function createStreamUpdateScheduler<TEvent = BaseEvent>(
  onFlush: (events: TEvent[]) => void,
  options: StreamUpdateSchedulerOptions = {},
): StreamUpdateScheduler<TEvent> {
  const flushDelayMs = options.flushDelayMs ?? DEFAULT_STREAM_FLUSH_MS;
  const setTimer = options.setTimer ?? ((callback, delayMs) => setTimeout(callback, delayMs));
  const clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer));

  let pending: TEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clearScheduledFlush = () => {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  };

  const flush = () => {
    clearScheduledFlush();
    if (pending.length === 0) return;
    const events = pending;
    pending = [];
    onFlush(events);
  };

  const scheduleFlush = () => {
    if (timer !== null) return;
    timer = setTimer(flush, flushDelayMs);
  };

  return {
    enqueue(event) {
      pending.push(event);
      scheduleFlush();
    },
    flush,
    dispose() {
      clearScheduledFlush();
      pending = [];
    },
  };
}
