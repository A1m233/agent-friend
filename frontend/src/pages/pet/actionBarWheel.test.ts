import { describe, expect, it } from "vitest";
import { createActionBarWheelPager, type ActionBarWheelEventData } from "./actionBarWheel";

const wheel = (deltaY: number, changes: Partial<ActionBarWheelEventData> = {}): ActionBarWheelEventData => ({
  deltaX: 0, deltaY, deltaMode: 0, ...changes,
});

describe("ActionBar wheel paging", () => {
  it.each([
    { event: wheel(120), expected: 1 },
    { event: wheel(-120), expected: -1 },
    { event: wheel(10, { deltaX: -80 }), expected: -1 },
    { event: wheel(-80, { deltaX: 10 }), expected: -1 },
    { event: wheel(80, { deltaX: -80 }), expected: 1 },
  ])("uses the dominant axis and its sign for $event", ({ event, expected }) => {
    expect(createActionBarWheelPager()(event, 0, true, true)).toBe(expected);
  });

  it("accumulates small touchpad deltas without losing fractional pixels", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(9.5), 0, true, true)).toBe(0);
    expect(page(wheel(10.25), 15, true, true)).toBe(0);
    expect(page(wheel(19.75), 30, true, true)).toBe(0);
    expect(page(wheel(0.5), 45, true, true)).toBe(1);
  });

  it("does not add diagonal axes together", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(21, { deltaX: 21 }), 0, true, true)).toBe(0);
    expect(page(wheel(19), 10, true, true)).toBe(1);
  });

  it.each([
    { event: wheel(3, { deltaMode: 1 }), expected: 1 },
    { event: wheel(0, { deltaX: -3, deltaMode: 1 }), expected: -1 },
    { event: wheel(1, { deltaMode: 2 }), expected: 1 },
    { event: wheel(-1, { deltaMode: 2 }), expected: -1 },
  ])("normalizes line and page modes for $event", ({ event, expected }) => {
    expect(createActionBarWheelPager()(event, 0, true, true)).toBe(expected);
  });

  it("normalizes mixed input modes before accumulating", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(2, { deltaMode: 1 }), 0, true, true)).toBe(0);
    expect(page(wheel(8), 20, true, true)).toBe(1);
  });

  it("emits only one page for a continuous gesture including large momentum events", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(1000), 0, true, true)).toBe(1);
    for (const time of [100, 200, 300, 450, 600]) expect(page(wheel(1000), time, true, true)).toBe(0);
    expect(page(wheel(40), 760, true, true)).toBe(1);
  });

  it("starts a fresh gesture after 160ms of silence and discards its predecessor's remainder", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(39), 0, true, true)).toBe(0);
    expect(page(wheel(1), 160, true, true)).toBe(0);
    expect(page(wheel(39), 170, true, true)).toBe(1);
    expect(page(wheel(40), 329, true, true)).toBe(0);
    expect(page(wheel(40), 489, true, true)).toBe(1);
  });

  it("resets old accumulation on reversal and permits an intentional reverse page", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(30), 0, true, true)).toBe(0);
    expect(page(wheel(-15), 10, true, true)).toBe(0);
    expect(page(wheel(-25), 20, true, true)).toBe(-1);
    expect(page(wheel(-100), 30, true, true)).toBe(0);
    expect(page(wheel(40), 40, true, true)).toBe(1);
  });

  it("does not wrap beyond an edge or replay blocked momentum when availability changes", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(-100), 0, false, true)).toBe(0);
    expect(page(wheel(-100), 30, true, true)).toBe(0);
    expect(page(wheel(100), 40, true, false)).toBe(0);
    expect(page(wheel(100), 50, true, true)).toBe(0);
    expect(page(wheel(40), 210, true, true)).toBe(1);
  });

  it("ignores Ctrl+wheel without consuming or adding to the paging gesture", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(30), 0, true, true)).toBe(0);
    expect(page(wheel(-100, { ctrlKey: true }), 20, true, true)).toBe(0);
    expect(page(wheel(10), 40, true, true)).toBe(1);
    expect(page(wheel(100, { ctrlKey: true }), 180, true, true)).toBe(0);
    expect(page(wheel(40), 200, true, true)).toBe(1);
  });

  it("does not let invalid or empty inputs poison a later valid gesture", () => {
    const page = createActionBarWheelPager();
    expect(page(wheel(20), 0, true, true)).toBe(0);
    for (const event of [wheel(0), wheel(Number.NaN), wheel(100, { deltaX: Number.POSITIVE_INFINITY }), wheel(100, { deltaMode: 9 })]) {
      expect(page(event, 10, true, true)).toBe(0);
    }
    expect(page(wheel(100), Number.NaN, true, true)).toBe(0);
    expect(page(wheel(20), 20, true, true)).toBe(1);
  });

  it("resets when the supplied clock moves backwards and keeps factory instances independent", () => {
    const first = createActionBarWheelPager(), second = createActionBarWheelPager();
    expect(first(wheel(40), 500, true, true)).toBe(1);
    expect(first(wheel(40), 100, true, true)).toBe(1);
    expect(second(wheel(40), 101, true, true)).toBe(1);
  });
});
