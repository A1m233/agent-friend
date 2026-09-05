// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, ModelResponseProgress } from "@/types/chat";
import { MessageActivityTail } from "./MessagePrimaryActivity";
import type { MessagePrimaryActivity } from "./primaryActivity";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("MessageActivityTail", () => {
  let container: HTMLDivElement;
  let root: Root;
  const message: ChatMessage = {
    id: "m", runId: "r", role: "assistant", status: "streaming",
    createdAt: "2026-09-05T00:00:00Z", blocks: [],
  };
  const waiting: ModelResponseProgress = {
    phase: "waiting", afterTool: false, attempt: 1, maxAttempts: 2, activityVersion: 1,
  };
  const model: MessagePrimaryActivity = { surface: "message", kind: "model", messageId: "m", status: waiting };

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });
  const render = async (activity: MessagePrimaryActivity | null | undefined, value = message) => {
    await act(async () => root.render(createElement(MessageActivityTail, { message: value, activity })));
  };

  it("replaces waiting with tool follow-up/retry/stopping, then removes it on success", async () => {
    await render(model);
    expect(container.textContent).toBe("正在等待模型响应…");
    await render({ ...model, status: { ...waiting, afterTool: true } });
    expect(container.textContent).toBe("正在整理工具结果…");
    await render({ ...model, status: { ...waiting, phase: "retrying", attempt: 2 } });
    expect(container.textContent).toBe("连接不稳定，正在重试 1/1");
    await render({ surface: "message", kind: "run", messageId: "m", phase: "stopping", label: "正在取消当前任务，任务尚未结束…" });
    expect(container.querySelectorAll("[data-primary-activity]")).toHaveLength(1);
    expect(container.textContent).toBe("正在取消当前任务，任务尚未结束…");
    await render(model, { ...message, status: "complete", runOutcome: "completed" });
    expect(container.innerHTML).toBe("");
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => vi.advanceTimersByTime(20_000));
    expect(container.innerHTML).toBe("");
  });

  it.each([
    { surface: "message", kind: "tool", messageId: "m", toolCallId: "t" },
    { surface: "message", kind: "compaction", messageId: "m", activityId: "c" },
    { surface: "message", kind: "socialAuth", messageId: "m", toolCallId: "t", requestId: "a" },
    null,
  ] as const)("does not invent loading for a selected card/dock: %j", async (activity) => {
    await render(model);
    await render(activity);
    expect(container.innerHTML).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the old standalone streaming fallback only when activity projection is absent", async () => {
    await render(undefined, { ...message, runStatus: waiting });
    expect(container.textContent).toBe("正在等待模型响应…");
    await render(null, { ...message, runStatus: waiting });
    expect(container.innerHTML).toBe("");
    await render(undefined, { ...message, status: "complete" });
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["expired", "任务等待时间过长，已过期"],
    ["skipped", "本轮任务未执行"],
    ["rejected", "本轮任务未被接纳"],
    ["failed", "执行失败"],
  ] as const)("keeps %s result separate from stale loading", async (runOutcome, label) => {
    await render(model, { ...message, runOutcome });
    expect(container.textContent).toBe(label);
    expect(container.querySelector("[data-run-outcome]")).not.toBeNull();
    expect(container.querySelector("[data-message-activity-tail]")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("leaves stopped outcomes to the header and specific errors to the existing bubble", async () => {
    await render(model, { ...message, runOutcome: "cancelled" });
    expect(container.innerHTML).toBe("");
    await render(undefined, { ...message, status: "interrupted" });
    expect(container.innerHTML).toBe("");
    await render(model, { ...message, status: "error", error: "具体错误" });
    expect(container.innerHTML).toBe("");
    expect(vi.getTimerCount()).toBe(0);
  });
});
