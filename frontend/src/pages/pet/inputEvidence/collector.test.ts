import { describe, expect, it, vi } from "vitest";
import { INPUT_EVIDENCE_LIMITS, PetInputEvidenceCollector } from "./collector";
import type { HitObservation, InputEvent } from "./types";

const pageId = "b7ae4ef0-5ee9-4035-8957-df8a325e5a30";
const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

function fixture(sendOverride?: (events: InputEvent[]) => Promise<unknown>) {
  let now = 0;
  const batches: InputEvent[][] = [];
  const send = vi.fn(sendOverride ?? (async (events: InputEvent[]) => { batches.push(events); }));
  const collector = new PetInputEvidenceCollector({ pageInstanceId: pageId, now: () => now, send });
  return {
    collector, batches, send,
    setTime: (value: number) => { now = value; },
    events: () => batches.flat(),
    drain: async () => {
      for (let i = 0; i < 8; i++) { now += 250; collector.flush(); await settle(); }
    },
  };
}

function hit(sampleId: number, reason: HitObservation["reason"] = "outside"): HitObservation {
  return {
    reason, ignore: reason === "outside", commandSent: false, dragging: reason === "dragging",
    cursor: {
      x: sampleId, y: 30,
      diagnostic: {
        sampleId, dprRevision: 2, scale: 1.75, scaleSource: "webview",
        physicalX: sampleId * 1.75, physicalY: 52.5, originX: -2560, originY: 0,
      },
    },
    spriteBounds: { x: 10, y: 20, w: 50, h: 80 },
  };
}

describe("pet input evidence collector", () => {
  it("records the baseline without an anomaly or a debug flag and preserves request identity across reordered acknowledgements", async () => {
    const f = fixture();
    f.collector.observeDisplay(1.25, 4096, 1280);
    const first = f.collector.dprReport(1.75, 1.25);
    const second = f.collector.dprReport(1.25, 1.25);
    f.collector.acknowledge("dpr-ack", second, true);
    f.collector.acknowledge("dpr-ack", first, false);
    await f.drain();
    expect(first.pageInstanceId).toBe(second.pageInstanceId);
    expect(first.requestId).not.toBe(second.requestId);
    expect(f.events().filter((event) => event.kind === "dpr-report").map((event) => event.reportedDpr)).toEqual([1.75, 1.25]);
    expect(f.events().filter((event) => event.kind === "dpr-ack").map((event) => [event.requestId, event.outcome])).toEqual([[second.requestId, "ok"], [first.requestId, "error"]]);
    expect(f.events().find((event) => event.kind === "display-observed")).toMatchObject({ actualDpr: 1.25, viewport: { width: 4096, height: 1280 } });
    expect(f.events().filter((event) => event.kind === "page-start")).toHaveLength(1);
  });

  it("records reason changes even when no new ignore command was issued", async () => {
    const f = fixture();
    f.collector.observeHit(hit(1, "dragging"), 1.25);
    f.setTime(250);
    f.collector.observeHit(hit(2, "dom-hit"), 1.25);
    await f.drain();
    expect(f.events().filter((event) => event.kind === "hit-decision").map((event) => [event.reason, event.commandIssued])).toEqual([["dragging", false], ["dom-hit", false]]);
    expect(f.events().some((event) => event.kind === "passthrough-request")).toBe(false);
  });

  it("keeps the first real cursor transition after a quota-sampled initial state", async () => {
    const f = fixture();
    f.collector.observeHit({ reason: "initial", ignore: true, commandSent: true, dragging: false }, 1.25);
    f.setTime(16);
    f.collector.observeHit(hit(1), 1.25);
    f.setTime(250);
    f.collector.observeHit(hit(2), 1.25);
    await f.drain();
    const outside = f.events().find((event) => event.kind === "hit-decision" && event.reason === "outside");
    expect(outside).toMatchObject({ sampleId: 2, css: { x: 2, y: 30 }, physical: { x: 3.5, y: 52.5 } });
    expect(f.events().filter((event) => event.css !== undefined)).toHaveLength(1);
  });

  it("bounds 60 Hz stable cursor input, preserves comparisons, and does not make a low-frequency mouse track", async () => {
    const f = fixture();
    for (let sample = 0; sample < 360; sample++) {
      f.setTime(sample * (1000 / 60));
      f.collector.observeHit(hit(sample, "dragging"), 1.25);
      f.collector.flush();
      await settle();
    }
    await f.drain();
    const representatives = f.events().filter((event) => event.kind === "hit-decision" || event.kind === "cursor-observed");
    expect(representatives.length).toBeLessThanOrEqual(24);
    expect(representatives.filter((event) => event.css !== undefined)).toHaveLength(1);
    expect(representatives.filter((event) => event.spriteBounds !== undefined)).toHaveLength(1);
    expect(representatives.at(-1)).toMatchObject({ actualDpr: 1.25, scale: 1.75, scaleSource: "webview", dprRevision: 2 });
    expect(f.events().filter((event) => event.kind === "hit-summary").reduce((sum, event) => sum + (event.count ?? 0), 0)).toBe(360);
    for (const batch of f.batches) {
      expect(batch.length).toBeLessThanOrEqual(INPUT_EVIDENCE_LIMITS.batchEntries);
      expect(new TextEncoder().encode(JSON.stringify(batch)).length).toBeLessThanOrEqual(INPUT_EVIDENCE_LIMITS.batchBytes);
    }
  });

  it("never samples transition coordinates more than once per second", async () => {
    const f = fixture();
    for (let sample = 0; sample < 20; sample++) {
      f.setTime(sample * 250);
      f.collector.observeHit(hit(sample, sample % 2 ? "sprite-hit" : "outside"), 1.25);
      f.collector.flush();
      await settle();
    }
    await f.drain();
    const samples = f.events().filter((event) => event.css !== undefined);
    expect(samples).toHaveLength(5);
    for (let index = 1; index < samples.length; index++) {
      expect(samples[index].monotonicMs - samples[index - 1].monotonicMs).toBeGreaterThanOrEqual(1000);
    }
  });

  it("keeps pending transport and payload buffers bounded under 1000 events/second pressure", async () => {
    const f = fixture(() => new Promise(() => {}));
    f.collector.flush();
    for (let event = 0; event < 10_000; event++) {
      f.setTime(event);
      f.collector.dprReport(1.25, 1.25);
      f.collector.observeHit(hit(event), 1.25);
      f.collector.flush();
      expect(f.collector.budget.entries).toBeLessThanOrEqual(INPUT_EVIDENCE_LIMITS.bufferEntries);
      expect(f.collector.budget.bytes).toBeLessThanOrEqual(INPUT_EVIDENCE_LIMITS.bufferBytes);
    }
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.collector.budget.inFlight).toBe(true);
    expect(f.collector.budget.dropped).toBeGreaterThan(0);
  });

  it("does not retry failed events or preserve error text, and carries their loss into the next successful batch", async () => {
    const batches: InputEvent[][] = [];
    let attempt = 0;
    const f = fixture(async (events) => {
      batches.push(events);
      if (++attempt === 1) throw new Error("secret cookie file C:/private/location");
    });
    f.collector.dprReport(1.25, 1.25);
    f.collector.flush();
    await settle();
    f.setTime(100);
    f.collector.flush();
    expect(batches).toHaveLength(1);
    f.setTime(250);
    f.collector.flush();
    await settle();
    expect(batches).toHaveLength(2);
    expect(batches[1]).toHaveLength(1);
    expect(batches[1][0]).toMatchObject({ kind: "coverage-gap", droppedCount: 2 });
    expect(JSON.stringify(batches)).not.toContain("secret");
    expect(f.collector.budget.dropped).toBe(0);
  });

  it("isolates synchronous transport failures and keeps control-event bursts distinct from hit sampling", async () => {
    const f = fixture(() => { throw new Error("transport unavailable"); });
    expect(() => f.collector.flush()).not.toThrow();
    expect(f.collector.budget.inFlight).toBe(false);
    for (let index = 0; index < 1000; index++) f.collector.observeHit(hit(index), 1.25);
    const request = f.collector.dprReport(1.25, 1.25);
    expect(request.requestId).toBe(1);
    expect(f.collector.budget.entries).toBe(2);
    await settle();
  });

  it("emits a loss summary only after all older queued events, even across multiple batches", async () => {
    const f = fixture();
    for (let request = 0; request < 100; request++) f.collector.dprReport(1.25, 1.25);
    await f.drain();
    const events = f.events();
    expect(events.at(-1)).toMatchObject({ kind: "coverage-gap", droppedCount: 37 });
    expect(events).toHaveLength(65);
    for (let index = 1; index < events.length; index++) {
      expect(events[index].frontendSeq).toBeGreaterThan(events[index - 1].frontendSeq);
    }
    expect(events[0].kind).toBe("page-start");
  });

  it("does not retry or invent exact frontend losses when native reports partial acceptance", async () => {
    const f = fixture(async () => false);
    f.collector.dprReport(1.25, 1.25);
    f.collector.flush();
    await settle();
    f.setTime(250);
    f.collector.flush();
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.collector.budget.dropped).toBe(0);
    expect(f.collector.budget.inFlight).toBe(false);
    expect(f.send.mock.calls.flatMap(([events]) => events).some((event) => event.kind === "coverage-gap")).toBe(false);
  });

  it("constructs content-free fields, omits unavailable alpha results and invalid geometry", async () => {
    const f = fixture();
    const untrusted = {
      ...hit(1), domId: "secret", target: "secret", error: "secret", text: "secret",
      alphaTested: false, alphaHit: true,
      cursor: { ...hit(1).cursor!, x: NaN, y: Infinity, url: "secret" },
      spriteBounds: { x: 0, y: 0, w: Infinity, h: 20 },
    };
    f.collector.observeHit(untrusted, NaN);
    const metadata = f.collector.dprReport(Infinity, NaN);
    expect(metadata.actualDpr).toBeUndefined();
    expect(metadata.displayDpr).toBeUndefined();
    await f.drain();
    const observed = f.events().find((event) => event.kind === "hit-decision")!;
    expect(observed.alphaTested).toBe(false);
    expect(observed.alphaHit).toBeUndefined();
    expect(observed.css).toBeUndefined();
    expect(observed.spriteBounds).toBeUndefined();
    expect(JSON.stringify(f.events())).not.toContain("secret");
    expect(JSON.stringify(f.events())).not.toContain("null");
  });
});
