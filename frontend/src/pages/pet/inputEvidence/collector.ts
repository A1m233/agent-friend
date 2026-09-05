import type { HitObservation, HitReason, InputCorrelation, InputEvent, InputKind } from "./types";

export const INPUT_EVIDENCE_LIMITS = {
  eventBytes: 4 * 1024,
  bufferEntries: 64,
  bufferBytes: 256 * 1024,
  batchEntries: 16,
  batchBytes: 64 * 1024,
  flushIntervalMs: 250,
  stateIntervalMs: 250,
  coordinateIntervalMs: 1000,
  controlPerSecond: 32,
  controlBurst: 64,
} as const;

interface Options {
  pageInstanceId: string;
  now: () => number;
  send: (events: InputEvent[]) => Promise<unknown>;
}

interface ReasonSummary {
  count: number;
  firstSeq: number;
  lastSeq: number;
}

function finite(value: number | undefined, min = 0, max = 1_000_000): number | undefined {
  return value !== undefined && Number.isFinite(value) && value >= min && value <= max
    ? value
    : undefined;
}

function integer(value: number | undefined): number | undefined {
  return Number.isSafeInteger(value) && value! >= 0 ? value : undefined;
}

function point(x: number | undefined, y: number | undefined) {
  const safeX = finite(x, -1_000_000);
  const safeY = finite(y, -1_000_000);
  return safeX !== undefined && safeY !== undefined ? { x: safeX, y: safeY } : undefined;
}

function increment(value: number, by = 1): number {
  return Math.min(Number.MAX_SAFE_INTEGER, value + by);
}

/** Page-local, lossy observer. No business IPC, filesystem, DOM reads, or retries live here. */
export class PetInputEvidenceCollector {
  private sequence = 0;
  private requestSequence = 0;
  private displayRevision = 0;
  private actualDpr: number | undefined;
  private displayDpr: number | undefined;
  private displaySignature: string | undefined;
  private queue: { event: InputEvent; bytes: number }[] = [];
  private queueBytes = 0;
  private inFlight = false;
  private lastFlush = -Infinity;
  private lastState = -Infinity;
  private lastCoordinates = -Infinity;
  private lastHitKey: string | undefined;
  private controlTokens: number = INPUT_EVIDENCE_LIMITS.controlBurst;
  private controlAt: number;
  private summaryAt: number;
  private summaries = new Map<HitReason, ReasonSummary>();
  private dropped = 0;

  constructor(private readonly options: Options) {
    if (!/^[a-fA-F0-9-]{1,96}$/.test(options.pageInstanceId)) {
      throw new Error("Invalid diagnostic page identity");
    }
    this.controlAt = options.now();
    this.summaryAt = this.controlAt;
    this.control(this.event("page-start"));
  }

  private event(kind: InputKind): InputEvent {
    this.sequence = increment(this.sequence);
    return {
      source: "frontend",
      kind,
      pageInstanceId: this.options.pageInstanceId,
      frontendSeq: this.sequence,
      monotonicMs: finite(this.options.now(), 0, 1e15) ?? 0,
      displayRevision: this.displayRevision,
      actualDpr: this.actualDpr,
      displayDpr: this.displayDpr,
    };
  }

  private enqueue(event: InputEvent): void {
    const bytes = new TextEncoder().encode(JSON.stringify(event)).byteLength;
    if (bytes > INPUT_EVIDENCE_LIMITS.eventBytes ||
      this.queue.length >= INPUT_EVIDENCE_LIMITS.bufferEntries ||
      this.queueBytes + bytes > INPUT_EVIDENCE_LIMITS.bufferBytes) {
      this.dropped = increment(this.dropped);
      return;
    }
    this.queue.push({ event, bytes });
    this.queueBytes += bytes;
  }

  private control(event: InputEvent): void {
    const now = this.options.now();
    this.controlTokens = Math.min(INPUT_EVIDENCE_LIMITS.controlBurst,
      this.controlTokens + Math.max(0, now - this.controlAt) * INPUT_EVIDENCE_LIMITS.controlPerSecond / 1000);
    this.controlAt = now;
    if (this.controlTokens < 1) {
      this.dropped = increment(this.dropped);
      return;
    }
    this.controlTokens -= 1;
    this.enqueue(event);
  }

  observeDisplay(actualDpr: number, width: number, height: number): void {
    this.actualDpr = finite(actualDpr, 0, 64);
    const safeWidth = finite(width);
    const safeHeight = finite(height);
    const signature = `${this.actualDpr}:${safeWidth}:${safeHeight}`;
    if (signature === this.displaySignature) return;
    this.displaySignature = signature;
    this.displayRevision = increment(this.displayRevision);
    const event = this.event("display-observed");
    if (safeWidth !== undefined && safeHeight !== undefined) {
      event.viewport = { width: safeWidth, height: safeHeight };
    }
    this.control(event);
  }

  dprReport(dpr: number, actualDpr: number): InputCorrelation {
    this.actualDpr = finite(actualDpr, 0, 64);
    this.displayDpr = finite(dpr, 0, 64);
    const event = this.event("dpr-report");
    event.requestId = this.requestSequence = increment(this.requestSequence);
    event.reportedDpr = this.displayDpr;
    event.stage = "request";
    this.control(event);
    return this.correlation(event);
  }

  passthroughRequest(ignore: boolean, reason?: HitReason, sampleId?: number): InputCorrelation {
    const event = this.event("passthrough-request");
    event.requestId = this.requestSequence = increment(this.requestSequence);
    event.ignore = ignore;
    event.reason = reason;
    event.sampleId = integer(sampleId);
    event.stage = "request";
    this.control(event);
    return this.correlation(event);
  }

  private correlation(event: InputEvent): InputCorrelation {
    return {
      pageInstanceId: event.pageInstanceId,
      frontendSeq: event.frontendSeq,
      monotonicMs: event.monotonicMs,
      displayRevision: event.displayRevision,
      requestId: event.requestId,
      actualDpr: event.actualDpr,
      displayDpr: event.displayDpr,
      sampleId: event.sampleId,
    };
  }

  acknowledge(kind: "dpr-ack" | "passthrough-ack", request: InputCorrelation, ok: boolean): void {
    const event = this.event(kind);
    event.requestId = request.requestId;
    event.sampleId = request.sampleId;
    event.outcome = ok ? "ok" : "error";
    event.stage = "ack";
    if (!ok) event.errorKind = "invoke-failed";
    this.control(event);
  }

  observeHit(observation: HitObservation, actualDpr: number): void {
    this.actualDpr = finite(actualDpr, 0, 64);
    const now = this.options.now();
    const key = `${observation.reason}:${observation.ignore}:${observation.dragging}`;
    const changed = key !== this.lastHitKey;
    const event = this.event(changed ? "hit-decision" : "cursor-observed");
    const summary = this.summaries.get(observation.reason);
    if (summary) {
      summary.count = increment(summary.count);
      summary.lastSeq = event.frontendSeq;
    } else {
      this.summaries.set(observation.reason, {
        count: 1, firstSeq: event.frontendSeq, lastSeq: event.frontendSeq,
      });
    }
    // The cursor comparison and state transitions share one representative quota.
    if (now - this.lastState < INPUT_EVIDENCE_LIMITS.stateIntervalMs) return;
    this.lastState = now;
    this.lastHitKey = key;
    event.reason = observation.reason;
    event.ignore = observation.ignore;
    event.commandIssued = observation.commandSent;
    event.dragging = observation.dragging;
    event.alphaTested = observation.alphaTested === true;
    if (event.alphaTested && typeof observation.alphaHit === "boolean") event.alphaHit = observation.alphaHit;
    const cursor = observation.cursor;
    const diagnostic = cursor?.diagnostic;
    if (diagnostic) {
      event.sampleId = integer(diagnostic.sampleId);
      event.dprRevision = integer(diagnostic.dprRevision);
      event.scale = finite(diagnostic.scale, 0, 64);
      if (diagnostic.scaleSource === "webview" || diagnostic.scaleSource === "fallback") {
        event.scaleSource = diagnostic.scaleSource;
      }
    }
    // Coordinates are selected only at actual transitions, never on a timer or stable drag/hover.
    if (changed && cursor && now - this.lastCoordinates >= INPUT_EVIDENCE_LIMITS.coordinateIntervalMs) {
      this.lastCoordinates = now;
      event.css = point(cursor?.x, cursor?.y);
      event.physical = point(diagnostic?.physicalX, diagnostic?.physicalY);
      event.windowOrigin = point(diagnostic?.originX, diagnostic?.originY);
      const bounds = observation.spriteBounds;
      const origin = bounds ? point(bounds.x, bounds.y) : undefined;
      const width = finite(bounds?.w);
      const height = finite(bounds?.h);
      if (origin && width !== undefined && height !== undefined) {
        event.spriteBounds = { ...origin, width, height };
      }
    }
    this.enqueue(event);
  }

  /** Timer entry: a hung transport retains at most one batch and never accumulates promises. */
  flush(): void {
    const now = this.options.now();
    if (now - this.summaryAt >= 1000) {
      this.summaryAt = now;
      for (const [reason, summary] of this.summaries) {
        const event = this.event("hit-summary");
        event.reason = reason;
        event.count = summary.count;
        event.firstSeq = summary.firstSeq;
        event.lastSeq = summary.lastSeq;
        this.control(event);
      }
      this.summaries.clear();
    }
    if (this.inFlight || now - this.lastFlush < INPUT_EVIDENCE_LIMITS.flushIntervalMs) return;
    const events: InputEvent[] = [];
    let bytes = 2; // JSON array brackets and separators count against the batch budget.
    let carriedDrops = 0;
    while (this.queue.length && events.length < INPUT_EVIDENCE_LIMITS.batchEntries) {
      const next = this.queue[0];
      if (bytes + next.bytes + 1 > INPUT_EVIDENCE_LIMITS.batchBytes) break;
      this.queue.shift();
      this.queueBytes -= next.bytes;
      bytes += next.bytes + 1;
      events.push(next.event);
    }
    // Do not put a newly numbered gap ahead of older queued facts, including the next batch.
    // A gap waits until every older event has left the buffer; its counters stay bounded meanwhile.
    if (this.dropped && this.queue.length === 0 &&
      events.length < INPUT_EVIDENCE_LIMITS.batchEntries &&
      bytes + INPUT_EVIDENCE_LIMITS.eventBytes + 1 <= INPUT_EVIDENCE_LIMITS.batchBytes) {
      const gap = this.event("coverage-gap");
      gap.droppedCount = carriedDrops = this.dropped;
      this.dropped = 0;
      events.push(gap);
    }
    if (!events.length) return;
    this.lastFlush = now;
    this.inFlight = true;
    const failed = () => {
      this.dropped = increment(this.dropped, carriedDrops + events.length - Number(carriedDrops > 0));
    };
    try {
      // `false` means the native collector received the batch but could retain only part of it.
      // Exact rejection counts belong to native coverage; never fabricate an entire lost batch.
      void this.options.send(events).then(undefined, failed).finally(() => { this.inFlight = false; });
    } catch {
      failed();
      this.inFlight = false;
    }
  }

  end(): void {
    this.control(this.event("page-end"));
    this.flush();
  }

  /** Payload accounting for deterministic tests; never exposes captured facts to the UI. */
  get budget() {
    return { entries: this.queue.length, bytes: this.queueBytes, dropped: this.dropped, inFlight: this.inFlight };
  }
}
