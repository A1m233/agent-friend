import type { ChatMessage } from "@/types/chat";

export const SEARCH_HIGHLIGHT_NAME = "agent-friend-search-hit";

export interface TextMatchRange {
  start: number;
  end: number;
}

export function findSearchMessageIndex(
  messages: ChatMessage[],
  anchorEventUuid: string,
): number {
  return messages.findIndex(
    (message) =>
      message.id === anchorEventUuid ||
      message.blocks.some(
        (block) => block.kind === "text" && block.mid === anchorEventUuid,
      ),
  );
}

export function findCaseInsensitiveOccurrences(text: string, query: string): TextMatchRange[] {
  if (!query) return [];
  const pattern = new RegExp(escapeRegExp(query), "giu");
  return Array.from(text.matchAll(pattern), (match) => ({
    start: match.index,
    end: match.index + match[0].length,
  }));
}

export function findSearchAnchor(root: ParentNode, anchorEventUuid: string): HTMLElement | null {
  return (
    Array.from(root.querySelectorAll<HTMLElement>("[data-search-anchor]")).find(
      (element) => element.dataset.searchAnchor === anchorEventUuid,
    ) ?? null
  );
}

/** Quote/search hosts use display:contents; measure their rendered child box. */
export function scrollToRenderedAnchor(viewport: HTMLElement, anchor: HTMLElement, bottomInset: number): void {
  const candidates = [anchor, ...Array.from(anchor.children)];
  const rect = candidates.map((element) => element.getBoundingClientRect())
    .find((candidate) => candidate.width > 0 && candidate.height > 0);
  if (!rect) return;
  const viewportRect = viewport.getBoundingClientRect();
  const availableHeight = Math.max(0, viewportRect.height - bottomInset);
  viewport.scrollBy({ top: rect.top + Math.min(rect.height, availableHeight) / 2
    - (viewportRect.top + availableHeight / 2), behavior: "auto" });
}

export function installSearchTextHighlight(root: HTMLElement, query: string): (() => void) | null {
  if (typeof CSS === "undefined") {
    return null;
  }

  const registry = (CSS as typeof CSS & {
    highlights?: {
      set(name: string, value: unknown): void;
      delete(name: string): boolean;
    };
  }).highlights;
  const HighlightConstructor = (
    globalThis as typeof globalThis & {
      Highlight?: new (...ranges: Range[]) => unknown;
    }
  ).Highlight;
  if (!registry || !HighlightConstructor) return null;

  const ranges = collectTextRanges(root, query);
  if (ranges.length === 0) return null;
  registry.set(SEARCH_HIGHLIGHT_NAME, new HighlightConstructor(...ranges));
  return () => {
    registry.delete(SEARCH_HIGHLIGHT_NAME);
  };
}

function collectTextRanges(root: HTMLElement, query: string): Range[] {
  const roots: ParentNode[] = [root];
  for (let index = 0; index < roots.length; index += 1) {
    const current = roots[index];
    for (const element of current.querySelectorAll("*")) {
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
  return roots.flatMap((current) => rangesWithinRoot(current, query));
}

function rangesWithinRoot(root: ParentNode, query: string): Range[] {
  const document = root.ownerDocument ?? window.document;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const chunks: Array<{ node: Text; start: number; end: number }> = [];
  let text = "";
  let current = walker.nextNode();
  while (current) {
    if (current instanceof Text && current.data) {
      const start = text.length;
      text += current.data;
      chunks.push({ node: current, start, end: text.length });
    }
    current = walker.nextNode();
  }

  return findCaseInsensitiveOccurrences(text, query).flatMap((match) => {
    const startChunk = chunks.find((chunk) => match.start >= chunk.start && match.start < chunk.end);
    const endChunk = chunks.find(
      (chunk) => match.end > chunk.start && match.end <= chunk.end,
    );
    if (!startChunk || !endChunk) return [];
    const range = document.createRange();
    range.setStart(startChunk.node, match.start - startChunk.start);
    range.setEnd(endChunk.node, match.end - endChunk.start);
    return [range];
  });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
