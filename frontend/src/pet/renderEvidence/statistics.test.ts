import { describe, expect, it } from "vitest";
import { RenderStatistics } from "./statistics";

describe("render evidence boundaries", () => {
  it("does not mistake running ticker or offscreen work for screen render completion", () => {
    const stats = new RenderStatistics();
    stats.tick(10); stats.tick(30); stats.begin(31); stats.end(33, false);
    expect(stats.take()).toEqual({ ticks: 2, renders: 0, timerTicks: 0, maxRenderMs: 0, maxTickGapMs: 20 });
    stats.begin(40); stats.end(44, true);
    expect(stats.take()).toMatchObject({ ticks: 0, renders: 1, maxRenderMs: 4 });
    expect(stats.take().renders).toBe(0);
  });
  it("does not treat an unfinished render as a completed call", () => {
    const stats = new RenderStatistics(); stats.begin(10); stats.tick(20);
    expect(stats.take().renders).toBe(0);
  });
});
