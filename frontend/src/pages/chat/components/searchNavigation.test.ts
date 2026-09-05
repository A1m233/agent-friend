import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/types/chat";
import {
  findCaseInsensitiveOccurrences,
  findSearchMessageIndex,
  scrollToRenderedAnchor,
} from "./searchNavigation";

const messages: ChatMessage[] = [
  {
    id: "user-event",
    role: "user",
    createdAt: "2026-08-05T00:00:00Z",
    blocks: [{ kind: "text", mid: "user-event", text: "hello" }],
    status: "complete",
  },
  {
    id: "assistant-parent",
    role: "assistant",
    createdAt: "2026-08-05T00:00:01Z",
    blocks: [
      { kind: "text", mid: "assistant-first", text: "first" },
      { kind: "tool", toolCallId: "tool", name: "tool", args: "", status: "done" },
      { kind: "text", mid: "assistant-second", text: "second" },
    ],
    status: "complete",
  },
];

describe("global search message navigation", () => {
  it("centers the rendered child of display:contents above the composer", () => {
    const scrollBy = vi.fn();
    const viewport = { getBoundingClientRect: () => ({ top: 100, height: 700 }), scrollBy } as unknown as HTMLElement;
    const anchor = { getBoundingClientRect: () => ({ top: 0, width: 0, height: 0 }),
      children: [{ getBoundingClientRect: () => ({ top: 900, width: 400, height: 100 }) }] } as unknown as HTMLElement;
    scrollToRenderedAnchor(viewport, anchor, 200);
    expect(scrollBy).toHaveBeenCalledWith({ top: 600, behavior: "auto" });
  });

  it("does not scroll to a not-yet-rendered empty anchor", () => {
    const scrollBy = vi.fn();
    const viewport = { scrollBy } as unknown as HTMLElement;
    const anchor = { getBoundingClientRect: () => ({ width: 0, height: 0 }), children: [] } as unknown as HTMLElement;
    scrollToRenderedAnchor(viewport, anchor, 0);
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("maps user event ids and assistant text block ids to the virtual row", () => {
    expect(findSearchMessageIndex(messages, "user-event")).toBe(0);
    expect(findSearchMessageIndex(messages, "assistant-second")).toBe(1);
    expect(findSearchMessageIndex(messages, "tool")).toBe(-1);
    expect(findSearchMessageIndex(messages, "missing")).toBe(-1);
  });

  it("finds every contiguous case-insensitive match without splitting Chinese", () => {
    expect(findCaseInsensitiveOccurrences("Needle needle NEEDLE", "needle")).toEqual([
      { start: 0, end: 6 },
      { start: 7, end: 13 },
      { start: 14, end: 20 },
    ]);
    expect(findCaseInsensitiveOccurrences("这里有中文关键词", "中文关")).toEqual([
      { start: 3, end: 6 },
    ]);
  });
});
