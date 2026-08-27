import type { ChatMessage, ChatTextReference } from "@/types/chat";

export const REFERENCE_HIGHLIGHT_NAME = "agent-friend-quoted-text";

interface TextChunk {
  node: Text;
  start: number;
  end: number;
}

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
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const scopedRange = selection.getRangeAt(0);
  const startHost = quoteHostFor(scopedRange.startContainer);
  const endHost = quoteHostFor(scopedRange.endContainer);
  if (!startHost || startHost !== endHost) return null;

  const range = composedSelectionRange(selection, startHost) ?? scopedRange;
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

  const chunks = collectRenderedTextChunks(startHost);
  let start = boundaryOffset(chunks, range.startContainer, range.startOffset);
  let end = boundaryOffset(chunks, range.endContainer, range.endOffset);
  const projectedText = chunks.map((chunk) => chunk.node.data).join("");

  if (start === null || end === null || end <= start) {
    const selectedText = selection.toString();
    const matches = occurrences(projectedText, selectedText);
    if (matches.length !== 1) return null;
    [start, end] = matches[0];
  }
  if (start === null || end === null || end <= start) return null;
  const quotedText = projectedText.slice(start, end);
  if (!quotedText.trim()) return null;

  return {
    messageEventUuid,
    textBlockId,
    sourceRole: sourceRole as "user" | "assistant",
    quotedText,
    start,
    end,
    rect: range.getBoundingClientRect(),
  };
}

function composedSelectionRange(selection: Selection, host: HTMLElement): Range | null {
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

  const range = hostDocument(staticRange.startContainer).createRange();
  try {
    range.setStart(staticRange.startContainer, staticRange.startOffset);
    range.setEnd(staticRange.endContainer, staticRange.endOffset);
  } catch {
    return null;
  }
  return range;
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

  const chunks = collectRenderedTextChunks(host);
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
  const range = rangeForOffsets(chunks, start, end);
  if (!range) return null;
  registry.set(REFERENCE_HIGHLIGHT_NAME, new HighlightConstructor(range));
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

function collectRenderedTextChunks(host: HTMLElement): TextChunk[] {
  const nodes: Text[] = [];
  const walk = (node: Node) => {
    if (node instanceof HTMLElement && node.dataset.quoteExclude === "true") return;
    if (node instanceof Text) {
      if (node.data) nodes.push(node);
      return;
    }
    if (typeof HTMLSlotElement !== "undefined" && node instanceof HTMLSlotElement) {
      const assigned = node.assignedNodes({ flatten: true });
      for (const child of assigned.length ? assigned : Array.from(node.childNodes)) walk(child);
      return;
    }
    if (node instanceof Element && node.shadowRoot) {
      walk(node.shadowRoot);
      return;
    }
    for (const child of Array.from(node.childNodes)) walk(child);
  };
  walk(host);
  let offset = 0;
  return nodes.map((node) => {
    const chunk = { node, start: offset, end: offset + node.data.length };
    offset = chunk.end;
    return chunk;
  });
}

function boundaryOffset(chunks: TextChunk[], container: Node, offset: number): number | null {
  if (container instanceof Text) {
    const chunk = chunks.find((item) => item.node === container);
    return chunk ? chunk.start + Math.min(offset, chunk.node.data.length) : null;
  }
  const child = container.childNodes[offset] ?? null;
  if (child) {
    const next = chunks.find((item) => child === item.node || child.contains?.(item.node));
    if (next) return next.start;
  }
  const previous = offset > 0 ? container.childNodes[offset - 1] : null;
  if (previous) {
    const priorChunks = chunks.filter(
      (item) => previous === item.node || previous.contains?.(item.node),
    );
    if (priorChunks.length) return priorChunks.at(-1)?.end ?? null;
  }
  return null;
}

function rangeForOffsets(chunks: TextChunk[], start: number, end: number): Range | null {
  const startChunk = chunks.find((chunk) => start >= chunk.start && start < chunk.end);
  const endChunk = chunks.find((chunk) => end > chunk.start && end <= chunk.end);
  if (!startChunk || !endChunk) return null;
  const range = hostDocument(startChunk.node).createRange();
  range.setStart(startChunk.node, start - startChunk.start);
  range.setEnd(endChunk.node, end - endChunk.start);
  return range;
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
