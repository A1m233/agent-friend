import { describe, expect, it, vi } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import { createStreamUpdateScheduler } from "./streamUpdateScheduler";

function ev(delta: string): BaseEvent {
  return {
    type: EventType.TEXT_MESSAGE_CONTENT,
    messageId: "m1",
    delta,
  } as BaseEvent;
}

describe("createStreamUpdateScheduler", () => {
  it("coalesces stream events into one timed flush", () => {
    vi.useFakeTimers();
    const onFlush = vi.fn();
    const scheduler = createStreamUpdateScheduler(onFlush, { flushDelayMs: 33 });

    scheduler.enqueue(ev("你"));
    scheduler.enqueue(ev("好"));

    expect(onFlush).not.toHaveBeenCalled();

    vi.advanceTimersByTime(32);
    expect(onFlush).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith([ev("你"), ev("好")]);

    scheduler.dispose();
    vi.useRealTimers();
  });

  it("flushes pending events immediately and clears the scheduled timer", () => {
    vi.useFakeTimers();
    const onFlush = vi.fn();
    const scheduler = createStreamUpdateScheduler(onFlush, { flushDelayMs: 33 });

    scheduler.enqueue(ev("a"));
    scheduler.flush();

    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith([ev("a")]);

    vi.advanceTimersByTime(33);
    expect(onFlush).toHaveBeenCalledTimes(1);

    scheduler.dispose();
    vi.useRealTimers();
  });

  it("drops pending events on dispose", () => {
    vi.useFakeTimers();
    const onFlush = vi.fn();
    const scheduler = createStreamUpdateScheduler(onFlush, { flushDelayMs: 33 });

    scheduler.enqueue(ev("a"));
    scheduler.dispose();
    vi.advanceTimersByTime(33);

    expect(onFlush).not.toHaveBeenCalled();

    vi.useRealTimers();
  });
});
