// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ToolBlock } from "@/types/chat";
import { ToolCard } from "./ToolCard";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("ToolCard", () => {
  let container: HTMLDivElement;
  let root: Root;
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    globalThis.ResizeObserver = class ResizeObserverStub implements ResizeObserver {
      readonly observe = () => undefined;
      readonly unobserve = () => undefined;
      readonly disconnect = () => undefined;
    };
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    globalThis.ResizeObserver = originalResizeObserver;
  });

  it("renders parameters and results as independently bounded scroll areas", async () => {
    const block: ToolBlock = {
      kind: "tool",
      toolCallId: "search",
      name: "web_search",
      args: JSON.stringify({ query: "Codex" }),
      result: JSON.stringify({ results: ["one", "two"] }),
      status: "done",
    };

    await act(async () => root.render(createElement(ToolCard, { block, embedded: true })));

    const parameters = container.querySelector('section[aria-label="参数"]');
    const result = container.querySelector('section[aria-label="结果"]');
    expect(parameters).not.toBeNull();
    expect(result).not.toBeNull();
    expect(parameters?.textContent).toContain("Codex");
    expect(result?.textContent).toContain("one");
    expect(result?.querySelector("pre")?.textContent).toContain(
      '{\n  "results": [\n    "one",\n    "two"\n  ]\n}',
    );

    const scrollAreas = [...container.querySelectorAll('[data-slot="scroll-area"]')];
    expect(scrollAreas).toHaveLength(2);
    for (const scrollArea of scrollAreas) {
      expect(scrollArea.classList.contains("max-h-52")).toBe(true);
      const viewport = scrollArea.querySelector('[data-slot="scroll-area-viewport"]');
      expect(viewport?.classList.contains("max-h-52")).toBe(true);
      expect(viewport?.classList.contains("overscroll-auto")).toBe(true);
      expect(viewport?.classList.contains("overscroll-contain")).toBe(false);
    }
  });

  it("renders filesystem read content as text with paging metadata", async () => {
    const block: ToolBlock = {
      kind: "tool",
      toolCallId: "read",
      name: "filesystem_read_text",
      args: JSON.stringify({ relative_path: "notes.md" }),
      result: JSON.stringify({
        ok: true,
        path: "notes.md",
        content: "first\nsecond\n",
        encoding: "utf-8",
        start_line: 1,
        start_column: 0,
        end_line: 2,
        end_column: 7,
        total_lines: 4,
        truncated: true,
        next_start_line: 3,
        next_start_column: 0,
      }),
      status: "done",
    };

    await act(async () => root.render(createElement(ToolCard, { block, embedded: true })));

    const result = container.querySelector('section[aria-label="结果"]');
    expect(result?.querySelector("pre")?.textContent).toBe("first\nsecond\n");
    expect(result?.textContent).toContain("notes.md · UTF-8");
    expect(result?.textContent).toContain("已截断；下一段从第 3 行开始");
    expect(result?.textContent).not.toContain('"content"');
    expect(result?.textContent).not.toContain("\\n");
  });
});
