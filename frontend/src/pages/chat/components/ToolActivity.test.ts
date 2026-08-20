// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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

  it("does not repeat visual status markers on compact group and item rows", async () => {
    await render([tool("search", "web_search"), tool("fetch", "web_fetch")]);
    await click(container.querySelector("button") as HTMLButtonElement);

    expect(container.textContent).toContain("执行了 2 个步骤");
    expect(container.querySelectorAll("span.rounded-full")).toHaveLength(0);
    expect(container.textContent?.match(/执行了/g)).toHaveLength(1);
  });

  it("uses the full activity width so ellipsis measures the actual available space", async () => {
    await render([tool("search", "web_search")]);

    expect(container.firstElementChild?.classList.contains("w-full")).toBe(true);
    expect(container.firstElementChild?.classList.contains("max-w-[85%]")).toBe(false);
    expect(container.querySelector("button > span.block.truncate")).not.toBeNull();
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
});
