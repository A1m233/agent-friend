import { joinVoiceDraft } from "./draft";
import type { VoiceInputTranscript, VoiceTranscriptSpan } from "./types";

export interface VoiceDraftUpdate {
  text: string;
  caret: number;
  conflict: boolean;
}

function validSpans(text: string, spans?: VoiceTranscriptSpan[]): spans is VoiceTranscriptSpan[] {
  let end = 0;
  return Array.isArray(spans) && spans.length > 0 && spans.every((span) => {
    const valid = !!span && typeof span.text === "string" && span.text.length > 0
      && Number.isFinite(span.startMs) && Number.isFinite(span.endMs)
      && span.startMs >= end && span.endMs >= span.startMs;
    end = span?.endMs;
    return valid;
  }) && spans.map((span) => span.text).join("") === text;
}

/** Undefined means the backend revised an owned prefix without a safe boundary. */
function unownedTail(current: VoiceInputTranscript, owned: VoiceInputTranscript | null): string | undefined {
  if (!owned?.text) return current.text;
  if (current.text.startsWith(owned.text)) return current.text.slice(owned.text.length).trimStart();
  if (validSpans(owned.text, owned.spans) && validSpans(current.text, current.spans)) {
    const boundary = owned.spans.at(-1)!.endMs;
    // A segment straddling the boundary cannot be split using character counts.
    if (current.spans.some((span) => span.startMs < boundary && span.endMs > boundary)) return undefined;
    return current.spans.filter((span) => span.startMs >= boundary && span.endMs > boundary)
      .map((span) => span.text).join("").trimStart();
  }
  return undefined;
}

/** One recording's editing state. Lives with the recording, not a Composer mount. */
export class VoiceDraftSession {
  traceId = "";
  text: string;
  caret: number;
  private start: number;
  private end: number;
  private lastRendered: VoiceInputTranscript | null = null;
  private owned: VoiceInputTranscript | null = null;
  private pending: VoiceInputTranscript | null = null;
  private composing = false;
  private closed = false;
  private final = false;
  private seq = -1;

  constructor(readonly workspaceKey: string, text: string, caret: number) {
    this.text = text;
    this.caret = Math.max(0, Math.min(caret, text.length));
    this.start = this.end = this.caret;
  }

  edit(text: string, caret: number, composing = false): void {
    caret = Math.max(0, Math.min(caret, text.length));
    if (text !== this.text || caret !== this.caret || (composing && !this.composing)) {
      this.owned = this.lastRendered;
      this.text = text;
      this.caret = this.start = this.end = caret;
    }
    this.composing = composing;
  }

  close(): void {
    this.closed = true;
    this.pending = null;
  }

  private result(conflict = false): VoiceDraftUpdate {
    return { text: this.text, caret: this.caret, conflict };
  }

  flush(): VoiceDraftUpdate | null {
    const pending = this.pending;
    this.pending = null;
    return pending ? this.apply(pending) : null;
  }

  apply(event: VoiceInputTranscript): VoiceDraftUpdate {
    if (this.closed || this.final || event.targetWorkspaceKey !== this.workspaceKey
      || (this.traceId && this.traceId !== event.traceId) || event.seq <= this.seq) return this.result();
    this.traceId = event.traceId;
    if (this.composing) {
      if (!this.pending || (this.pending.kind !== "final" && event.seq > this.pending.seq)) this.pending = event;
      return this.result();
    }
    const tail = unownedTail(event, this.owned);
    if (tail === undefined) {
      this.close();
      return this.result(true);
    }
    this.seq = event.seq;
    this.final = event.kind === "final";
    this.lastRendered = event;
    if (!tail && this.start === this.end) return this.result();
    const prefix = this.text.slice(0, this.start);
    const suffix = this.text.slice(this.end);
    this.text = tail ? joinVoiceDraft(joinVoiceDraft(prefix, tail), suffix) : prefix + suffix;
    this.caret = this.end = this.text.length - suffix.length;
    return this.result();
  }
}
