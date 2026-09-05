// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToolBlock } from "@/types/chat";
import { ToolActivity } from "./ToolActivity";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

function tool(id: string, name: string, result = "ok"): ToolBlock {
  return {
    kind: "tool",
    toolCallId: id,
    name,
    args: JSON.stringify({ query: "Codex" }),
    result,
    status: "done",
  };
}

describe("ToolActivity", () => {
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

  async function render(blocks: ToolBlock[]) {
    await act(async () => root.render(createElement(ToolActivity, { blocks })));
  }

  async function click(button: HTMLButtonElement) {
    await act(async () => button.click());
  }

  it("renders one compact semantic row and reveals the existing details on demand", async () => {
    await render([tool("search", "web_search", "search result")]);

    const trigger = container.querySelector("button");
    expect(trigger).toBeInstanceOf(HTMLButtonElement);
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).toContain("搜索网页「Codex」");
    expect(container.textContent).not.toContain("已完成");
    expect(container.querySelector("span.rounded-full")).toBeNull();
    expect(container.textContent).not.toContain("search result");

    await click(trigger as HTMLButtonElement);

    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("search result");
  });

  it("announces a user disclosure change before expanding its content", async () => {
    const onDisclosureChange = vi.fn(() => {
      expect(container.textContent).not.toContain("search result");
    });
    const blocks = [tool("search", "web_search", "search result")];
    await act(async () => root.render(createElement(ToolActivity, {
      blocks,
      onDisclosureChange,
    })));

    await click(container.querySelector("button") as HTMLButtonElement);

    expect(onDisclosureChange).toHaveBeenCalledOnce();
    expect(container.textContent).toContain("search result");
  });

  it("announces both group and nested disclosure changes", async () => {
    const onDisclosureChange = vi.fn();
    const blocks = [
      tool("search", "web_search", "search result"),
      tool("fetch", "web_fetch", "page result"),
    ];
    await act(async () => root.render(createElement(ToolActivity, {
      blocks,
      onDisclosureChange,
    })));

    await click(container.querySelector("button") as HTMLButtonElement);
    await click(container.querySelectorAll("button")[1] as HTMLButtonElement);

    expect(onDisclosureChange).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("search result");
  });

  it("does not repeat visual status markers on compact group and item rows", async () => {
    await render([tool("search", "web_search"), tool("fetch", "web_fetch")]);
    await click(container.querySelector("button") as HTMLButtonElement);

    expect(container.textContent).toContain("执行了 2 个步骤");
    expect(container.querySelectorAll("span.rounded-full")).toHaveLength(0);
    expect(container.textContent?.match(/执行了/g)).toHaveLength(1);
  });

  it("does not invent query activity for a calculation-only group", async () => {
    await render([
      tool("calculation-1", "calculate"),
      tool("calculation-2", "calculate"),
      tool("calculation-3", "calculate"),
      tool("calculation-4", "calculate"),
    ]);

    expect(container.textContent).toContain("执行了 4 个步骤：计算");
    expect(container.textContent).not.toContain("查询");
    expect(
      container
        .querySelector('[data-slot="tool-activity-icon"]')
        ?.classList.contains("lucide-calculator"),
    ).toBe(true);
  });

  it("lets the compact trigger fit its content while keeping the activity width available", async () => {
    await render([tool("search", "web_search")]);

    expect(container.firstElementChild?.classList.contains("w-full")).toBe(true);
    expect(container.firstElementChild?.classList.contains("max-w-[85%]")).toBe(false);
    const trigger = container.querySelector("button");
    expect(trigger?.classList.contains("inline-flex")).toBe(true);
    expect(trigger?.classList.contains("max-w-full")).toBe(true);
    expect(trigger?.classList.contains("w-full")).toBe(false);
    expect(container.querySelector("button > span.block.truncate")).not.toBeNull();
  });

  it("keeps icon and summary colors aligned while revealing the trailing chevron on hover", async () => {
    await render([tool("search", "web_search"), tool("read", "web_fetch")]);

    const groupTrigger = container.querySelector("button") as HTMLButtonElement;
    const groupIcon = groupTrigger.querySelector('[data-slot="tool-activity-icon"]');
    const groupSummary = groupTrigger.querySelector("span.block.truncate");
    const groupChevron = groupTrigger.querySelector('[data-slot="tool-activity-chevron"]');
    expect(groupTrigger.firstElementChild).toBe(groupIcon);
    expect(groupTrigger.lastElementChild).toBe(groupChevron);
    expect(groupTrigger.classList.contains("text-muted")).toBe(true);
    expect(groupTrigger.classList.contains("hover:text-fg")).toBe(true);
    expect(groupIcon?.classList.contains("lucide-search")).toBe(true);
    expect(groupSummary?.classList.contains("text-fg")).toBe(false);
    expect(groupChevron?.classList.contains("opacity-0")).toBe(true);
    expect(groupChevron?.classList.contains("group-hover:opacity-100")).toBe(true);
    expect(groupChevron?.classList.contains("group-focus-visible:opacity-100")).toBe(true);

    await click(groupTrigger);

    const itemTriggers = [...container.querySelectorAll("button")].slice(1);
    expect(
      itemTriggers[0]
        .querySelector('[data-slot="tool-activity-icon"]')
        ?.classList.contains("lucide-search"),
    ).toBe(true);
    expect(
      itemTriggers[1]
        .querySelector('[data-slot="tool-activity-icon"]')
        ?.classList.contains("lucide-file-text"),
    ).toBe(true);
  });

  it("uses the other-category icon for unknown tools", async () => {
    await render([tool("unknown", "future_tool")]);

    expect(
      container
        .querySelector('[data-slot="tool-activity-icon"]')
        ?.classList.contains("lucide-wrench"),
    ).toBe(true);
  });

  it("keeps the activity and first item open when streaming grows one tool into a group", async () => {
    const first = tool("search", "web_search", "search result");
    await render([first]);
    await click(container.querySelector("button") as HTMLButtonElement);

    await render([first, tool("fetch", "web_fetch", "page result")]);

    const triggers = [...container.querySelectorAll("button")];
    expect(triggers).toHaveLength(3);
    expect(triggers[0].getAttribute("aria-expanded")).toBe("true");
    expect(triggers[1].getAttribute("aria-expanded")).toBe("true");
    expect(triggers[2].getAttribute("aria-expanded")).toBe("false");
    expect(container.textContent).toContain("search result");
    expect(container.textContent).toContain("查看网页内容");
  });

  it("does not reset group openness when a tool status changes", async () => {
    const first = tool("search", "web_search");
    const running = { ...tool("fetch", "web_fetch"), status: "running" as const, result: undefined };
    await render([first, running]);
    await click(container.querySelector("button") as HTMLButtonElement);

    await render([first, { ...running, status: "done", result: "done" }]);

    const trigger = container.querySelector("button");
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("执行了 2 个步骤");
  });

  it("marks only the selected managed tool group as the primary activity", async () => {
    const first = { ...tool("search", "web_search"), status: "running" as const, result: undefined };
    const second = { ...tool("fetch", "web_fetch"), status: "running" as const, result: undefined };

    await act(async () => root.render(createElement(ToolActivity, {
      blocks: [first, second],
      activityManaged: true,
    })));
    expect(container.textContent).toContain("执行 2 个步骤");
    expect(container.textContent).not.toContain("正在执行 2 个步骤");
    expect(container.querySelector("[data-primary-activity]")).toBeNull();

    await act(async () => root.render(createElement(ToolActivity, {
      blocks: [first, second],
      activityManaged: true,
      primaryToolCallId: "fetch",
    })));
    expect(container.textContent).toContain("正在执行 2 个步骤");
    expect(container.querySelector("[data-primary-activity='tool']")).not.toBeNull();
  });

  it("does not revive live emphasis when unselected tool details are expanded", async () => {
    const block = { ...tool("search", "web_search"), status: "running" as const };
    await act(async () => root.render(createElement(ToolActivity, {
      blocks: [block], activityManaged: true,
    })));
    await click(container.querySelector("button") as HTMLButtonElement);
    expect(container.textContent).toContain("参数");
    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(container.querySelector("[data-primary-activity]")).toBeNull();
  });
});
