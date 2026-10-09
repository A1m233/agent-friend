import type { ChatMessage, ChatTextReference } from "@/types/chat";

export const REFERENCE_HIGHLIGHT_NAME = "agent-friend-quoted-text";
const RECT_TOLERANCE_PX = 1;
const LINE_TOLERANCE_PX = 3;

interface TextChunk {
  node: Text;
  start: number;
  end: number;
}

interface TextSpan { start: number; end: number }

export interface CapturedQuoteSelection {
  messageEventUuid: string;
  textBlockId: string;
  sourceRole: "user" | "assistant";
  quotedText: string;
  start: number;
  end: number;
  rect: DOMRect;
}

export function captureQuoteSelection(selection: Selection | null): CapturedQuoteSelection | null {
  if (!selection || selection.rangeCount === 0) return null;
  // WebView2 may report a collapsed outer host and only a newline for a visible
  // shadow selection. Resolve composed boundaries before judging it empty.
  const selectedText = selection.toString();
  const scopedRange = selection.getRangeAt(0);
  const startHost = quoteHostFor(scopedRange.startContainer);
  const endHost = quoteHostFor(scopedRange.endContainer);
  if (!startHost || startHost !== endHost) return null;

  const composedRange = composedSelectionRange(selection, startHost);
  const range = composedRange ?? scopedRange;
  const composedStartHost = quoteHostFor(range.startContainer);
  const composedEndHost = quoteHostFor(range.endContainer);
  if (composedStartHost !== startHost || composedEndHost !== startHost) return null;
  if (
    isQuoteExcluded(range.startContainer, startHost)
    || isQuoteExcluded(range.endContainer, startHost)
  ) return null;

  const messageEventUuid = startHost.dataset.quoteMessageId;
  const textBlockId = startHost.dataset.quoteBlockId;
  const sourceRole = startHost.dataset.quoteRole;
  if (!messageEventUuid || !textBlockId || !["user", "assistant"].includes(sourceRole ?? "")) {
    return null;
  }

  const { chunks, spans } = collectRenderedTextProjection(startHost);
  let start = boundaryOffset(spans, range.startContainer, range.startOffset);
  let end = boundaryOffset(spans, range.endContainer, range.endOffset);
  const projectedText = chunks.map((chunk) => chunk.node.data).join("");
  const retargetedToHost = !composedRange && collectOpenShadowRoots(startHost).length > 0
    && [range.startContainer, range.endContainer].some(node =>
      !(node instanceof Text) && node.getRootNode() === startHost.getRootNode());

  if (start === null || end === null || end <= start
    || (retargetedToHost && projectedText.slice(start, end) !== selectedText)) {
    const matches = occurrences(projectedText, selectedText);
    if (matches.length !== 1) return null;
    [start, end] = matches[0];
  }
  if (start === null || end === null || end <= start) return null;
  const quotedText = projectedText.slice(start, end);
  if (!quotedText.trim()) return null;
  // A retargeted Shadow DOM selection can recover exact text offsets while its original Range
  // still covers the outer markdown host. Rebuild a live text Range before measuring so floating
  // UI is anchored to the selected glyphs rather than to the host element.
  const measurementRanges = rangesForOffsets(chunks, start, end);
  if (!measurementRanges.length) return null;

  return {
    messageEventUuid,
    textBlockId,
    sourceRole: sourceRole as "user" | "assistant",
    quotedText,
    start,
    end,
    rect: firstSelectedLineRect(measurementRanges),
  };
}

function firstSelectedLineRect(ranges: Range[]): DOMRect {
  const rects = ranges.flatMap(range => typeof range.getClientRects === "function" ? Array.from(range.getClientRects()) : []);
  const visibleRects = rects.filter((rect) => rect.width > 0 && rect.height > 0);
  const textRects = visibleRects.filter((candidate, candidateIndex) => (
    !visibleRects.some((other, otherIndex) => (
      candidateIndex !== otherIndex
      && rectArea(other) < rectArea(candidate)
      && rectContains(candidate, other)
    ))
  ));
  textRects.sort((a, b) => (
    Math.abs(a.top - b.top) <= LINE_TOLERANCE_PX ? a.left - b.left : a.top - b.top
  ));
  const first = textRects[0];
  if (!first) return ranges[0].getBoundingClientRect();

  // Inline markdown commonly splits one visual line into several client rects (plain text,
  // <strong>, <a>, <code>, ...). Center over their union, not just the first fragment.
  const firstLineRects = textRects.filter((rect) => sameVisualLine(first, rect));
  const left = Math.min(...firstLineRects.map((rect) => rect.left));
  const top = Math.min(...firstLineRects.map((rect) => rect.top));
  const right = Math.max(...firstLineRects.map((rect) => rect.right));
  const bottom = Math.max(...firstLineRects.map((rect) => rect.bottom));
  return DOMRect.fromRect({ x: left, y: top, width: right - left, height: bottom - top });
}

function rectArea(rect: DOMRect): number {
  return rect.width * rect.height;
}

function rectContains(outer: DOMRect, inner: DOMRect): boolean {
  return (
    outer.left <= inner.left + RECT_TOLERANCE_PX
    && outer.top <= inner.top + RECT_TOLERANCE_PX
    && outer.right >= inner.right - RECT_TOLERANCE_PX
    && outer.bottom >= inner.bottom - RECT_TOLERANCE_PX
  );
}

function sameVisualLine(anchor: DOMRect, candidate: DOMRect): boolean {
  if (Math.abs(anchor.top - candidate.top) <= LINE_TOLERANCE_PX) return true;
  const overlap = Math.min(anchor.bottom, candidate.bottom) - Math.max(anchor.top, candidate.top);
  return overlap > Math.min(anchor.height, candidate.height) / 2;
}

function composedSelectionRange(selection: Selection, host: HTMLElement): StaticRange | null {
  const getComposedRanges = (
    selection as Partial<Pick<Selection, "getComposedRanges">>
  ).getComposedRanges;
  if (typeof getComposedRanges !== "function") return null;

  const shadowRoots = collectOpenShadowRoots(host);
  if (shadowRoots.length === 0) return null;

  let staticRange: StaticRange | undefined;
  try {
    [staticRange] = getComposedRanges.call(selection, { shadowRoots });
  } catch {
    return null;
  }
  if (!staticRange) return null;

  // A StaticRange may cross shadow trees. A live Range cannot: setting its second
  // endpoint silently collapses it. Keep both boundaries until text projection.
  return staticRange;
}

function collectOpenShadowRoots(root: Node): ShadowRoot[] {
  const roots: ShadowRoot[] = [];
  const walk = (node: Node) => {
    if (node instanceof Element && node.shadowRoot) {
      roots.push(node.shadowRoot);
      walk(node.shadowRoot);
    }
    for (const child of Array.from(node.childNodes)) walk(child);
  };
  walk(root);
  return roots;
}

export function findReferenceMessageIndex(
  messages: ChatMessage[],
  reference: ChatTextReference,
): number {
  const anchor = reference.source.messageEventUuid;
  const blockId = reference.source.textBlockId;
  return messages.findIndex(
    (message) =>
      message.id === anchor
      || message.blocks.some((block) => block.kind === "text" && block.mid === blockId),
  );
}

export function findQuoteBlockHost(root: ParentNode, blockId: string): HTMLElement | null {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-quote-block-id]")).find(
    (element) => element.dataset.quoteBlockId === blockId,
  ) ?? null;
}

export function installReferenceHighlight(
  host: HTMLElement,
  reference: ChatTextReference,
): (() => void) | null {
  if (
    reference.availability !== "available"
    || !reference.quotedText
    || typeof CSS === "undefined"
  ) return null;
  const registry = (CSS as typeof CSS & {
    highlights?: {
      set(name: string, value: unknown): void;
      delete(name: string): boolean;
    };
  }).highlights;
  const HighlightConstructor = (
    globalThis as typeof globalThis & { Highlight?: new (...ranges: Range[]) => unknown }
  ).Highlight;
  if (!registry || !HighlightConstructor) return null;

  const { chunks } = collectRenderedTextProjection(host);
  const text = chunks.map((chunk) => chunk.node.data).join("");
  let start = reference.locator.start;
  let end = reference.locator.end;
  const exactOffset =
    reference.locator.kind === "text_range"
    && start !== null
    && end !== null
    && text.slice(start, end) === reference.quotedText;
  if (!exactOffset) {
    const matches = occurrences(text, reference.quotedText);
    if (matches.length !== 1) return null;
    [start, end] = matches[0];
  }
  if (start === null || end === null) return null;
  const ranges = rangesForOffsets(chunks, start, end);
  if (!ranges.length) return null;
  registry.set(REFERENCE_HIGHLIGHT_NAME, new HighlightConstructor(...ranges));
  return () => {
    registry.delete(REFERENCE_HIGHLIGHT_NAME);
  };
}

function quoteHostFor(node: Node): HTMLElement | null {
  let current: Node | null = node;
  while (current) {
    if (current instanceof HTMLElement && current.dataset.quoteableText === "true") {
      return current;
    }
    current = composedParent(current);
  }
  return null;
}

function isQuoteExcluded(node: Node, host: HTMLElement): boolean {
  let current: Node | null = node;
  while (current && current !== host) {
    if (current instanceof HTMLElement && current.dataset.quoteExclude === "true") return true;
    current = composedParent(current);
  }
  return false;
}

function composedParent(node: Node): Node | null {
  if (node.parentNode) return node.parentNode;
  const root = node.getRootNode();
  return typeof ShadowRoot !== "undefined" && root instanceof ShadowRoot ? root.host : null;
}

function collectRenderedTextProjection(host: HTMLElement) {
  const chunks: TextChunk[] = [];
  const spans = new Map<Node, TextSpan>();
  let offset = 0;
  const walk = (node: Node) => {
    if (node instanceof HTMLElement && node.dataset.quoteExclude === "true") return;
    const start = offset;
    if (node instanceof Text) {
      offset += node.data.length;
      if (node.data) chunks.push({ node, start, end: offset });
      spans.set(node, { start, end: offset });
      return;
    }
    if (typeof HTMLSlotElement !== "undefined" && node instanceof HTMLSlotElement) {
      const assigned = node.assignedNodes({ flatten: true });
      for (const child of assigned.length ? assigned : Array.from(node.childNodes)) walk(child);
    } else if (node instanceof Element && node.shadowRoot) {
      walk(node.shadowRoot);
    } else {
      for (const child of Array.from(node.childNodes)) walk(child);
    }
    // Empty layout/slot nodes still have a position in the rendered text stream.
    spans.set(node, { start, end: offset });
  };
  walk(host);
  return { chunks, spans };
}

function boundaryOffset(spans: Map<Node, TextSpan>, container: Node, offset: number): number | null {
  const span = spans.get(container);
  if (!span) return null;
  if (container instanceof Text) {
    return span.start + Math.min(offset, container.length);
  }
  const child = container.childNodes[offset] ?? null;
  if (child && spans.has(child)) return spans.get(child)!.start;
  const previous = offset > 0 ? container.childNodes[offset - 1] : null;
  if (previous && spans.has(previous)) return spans.get(previous)!.end;
  if (offset === 0) return span.start;
  if (offset === container.childNodes.length) return span.end;
  return null;
}

function rangesForOffsets(chunks: TextChunk[], start: number, end: number): Range[] {
  // Keep live measurement/highlight ranges within individual text nodes, even
  // when the logical quote spans multiple component shadow trees.
  return chunks.filter(chunk => chunk.end > start && chunk.start < end).map(chunk => {
    const range = hostDocument(chunk.node).createRange();
    range.setStart(chunk.node, Math.max(0, start - chunk.start));
    range.setEnd(chunk.node, Math.min(chunk.node.length, end - chunk.start));
    return range;
  });
}

function hostDocument(node: Node): Document {
  return node.ownerDocument ?? window.document;
}

function occurrences(text: string, query: string): Array<[number, number]> {
  const matches: Array<[number, number]> = [];
  let cursor = 0;
  while (query && cursor <= text.length - query.length) {
    const start = text.indexOf(query, cursor);
    if (start < 0) break;
    matches.push([start, start + query.length]);
    cursor = start + Math.max(1, query.length);
  }
  return matches;
}
