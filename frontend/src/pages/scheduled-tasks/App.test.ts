// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ScheduledTask } from "@/services/api/scheduledTasks";
import { formatTimestamp } from "@/components/scheduling/presentation";

const mocks = vi.hoisted(() => ({ get: vi.fn(), list: vi.fn(), remove: vi.fn(), editor: vi.fn(), history: vi.fn(), taskId: "t" as string | null }));
vi.mock("@/lib/model-config", () => ({
  useModelCatalogStore: Object.assign(() => ({ providers: [{ label: "Test connection", models: [{ id: "custom/connection/model", label: "Readable model" }] }] }), {
    getState: () => ({ load: async () => {} }),
  }),
}));
vi.mock("./navigation", () => ({
  useScheduledNavigation: (select: (state: { taskId: string | null }) => unknown) =>
    select({ taskId: mocks.taskId }),
  openScheduledTask: vi.fn(),
  openScheduledMessage: vi.fn(),
}));
vi.mock("@/services/api/scheduledTasks", () => ({
  scheduledTasksApi: {
    get: mocks.get,
    list: mocks.list,
    remove: mocks.remove,
    history: mocks.history,
  },
}));
const defaultHistory = {
      occurrences: [
        {
          id: "run-1",
          state: "terminal",
          outcome: "skipped",
          reason_code: "task_run_in_progress",
          scheduled_at_utc: "2026-10-06T00:00:00Z",
          started_at: null,
          finished_at: null,
          session_id: null,
          run_id: null,
          user_event_id: null,
          waiting_for_approval: false,
        },
      ],
    };
vi.mock("@/services/api/sessions", () => ({
  sessionsApi: { list: vi.fn(async () => []) },
}));
vi.mock("./TaskEditor", () => ({
  TaskEditor: (props: { task: ScheduledTask }) => {
    mocks.editor(props.task);
    return null;
  },
}));

import { ScheduledTasksApp } from "./App";

it("refreshes quietly every 30 seconds without flashing the footer", async () => {
  vi.useFakeTimers();
  mocks.taskId = null;
  mocks.list.mockResolvedValue({ tasks: [{ id: "t", name: "Task", status: "active" }] });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(ScheduledTasksApp, { active: true })));
    const footer = () => container.querySelector('[role="status"]')?.textContent;
    expect(footer()).toBe("已显示全部");
    await act(async () => vi.advanceTimersByTimeAsync(29999));
    expect(mocks.list).toHaveBeenCalledTimes(1);
    let finish!: (value: unknown) => void;
    mocks.list.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(footer()).toBe("已显示全部");
    await act(async () => finish({ tasks: [{ id: "t", name: "Updated task", status: "paused" }] }));
    expect(container.textContent).toContain("Updated task");
    expect(footer()).toBe("已显示全部");
  } finally {
    await act(async () => root.unmount());
  }
});

it("shows readable model names, closure reason and only actual start time", async () => {
  mocks.get.mockResolvedValue({ id: "t", name: "Task", status: "active", schedule: {}, request_options: { attachments: [], model: { model_id: "custom/connection/model" } } });
  const started = "2026-10-07T06:00:01Z";
  mocks.history.mockResolvedValue({ occurrences: [{
    ...defaultHistory.occurrences[0], outcome: "interrupted", reason_code: "runtime_restarted",
    started_at: started, finished_at: "2026-10-07T08:49:21Z",
    effective_options: { model: { model_id: "custom/connection/model" } },
  }] });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(ScheduledTasksApp, { active: true })));
    expect(container.querySelector("header")?.textContent).toContain("已启用");
    expect(container.textContent).toContain("Readable model");
    expect(container.textContent).not.toContain("custom/connection/model");
    expect(container.textContent).toContain("应用关闭，本次执行已中断");
    expect(container.textContent).toContain(`开始：${formatTimestamp(started)}`);
    expect(container.textContent).not.toContain("结束：");
    expect(container.textContent).not.toContain(new Date(defaultHistory.occurrences[0].scheduled_at_utc).toLocaleString());
  } finally {
    await act(async () => root.unmount());
  }
});

let intersect: () => void;
beforeEach(() => {
  mocks.taskId = "t";
  vi.clearAllMocks();
  mocks.history.mockResolvedValue(defaultHistory);
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
      intersect = () => callback([{ isIntersecting: true }]);
    }
    observe() {}
    disconnect() {}
  });
});

it("uses an alert dialog whose cancel preserves the task and whose confirmation removes it once", async () => {
  mocks.get.mockResolvedValue({ id: "t", name: "Task", status: "active", schedule: {}, request_options: { attachments: [] } });
  mocks.remove.mockResolvedValue({});
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const deleteButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="删除"]')!;
  try {
    await act(async () => root.render(createElement(ScheduledTasksApp, { active: true })));
    await act(async () => deleteButton().click());
    let dialog = document.querySelector('[role="alertdialog"]')!;
    expect(dialog.textContent).toContain("删除定时任务");
    const cancel = [...dialog.querySelectorAll("button")].find((button) => button.textContent === "取消")!;
    expect(document.activeElement).toBe(cancel);
    await act(async () => cancel.click());
    expect(mocks.remove).not.toHaveBeenCalled();
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
    await act(async () => deleteButton().click());
    dialog = document.querySelector('[role="alertdialog"]')!;
    await act(async () => [...dialog.querySelectorAll("button")].find((button) => button.textContent === "删除")!.click());
    expect(mocks.remove).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  } finally {
    await act(async () => root.unmount());
  }
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

it("appends on intersection once, retains loaded rows on refresh, and ignores an old page after navigation", async () => {
  vi.useFakeTimers();
  mocks.taskId = null;
  let names = Array.from({ length: 23 }, (_, i) => `task-${i}`);
  mocks.list.mockImplementation(async (offset: number, limit: number) => ({ tasks: names.slice(offset, offset + limit).map((name) => ({ id: name, name, status: "active" })) }));
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(createElement(ScheduledTasksApp, { active: true })));
    expect(container.textContent).toContain("task-19");
    expect(container.textContent).not.toContain("task-20");
    expect(container.textContent).not.toContain("下一页");
    await act(async () => { intersect(); intersect(); });
    expect(container.textContent).toContain("task-0");
    expect(container.textContent).toContain("task-22");
    expect(mocks.list.mock.calls.filter(([offset]) => offset === 20)).toHaveLength(1);
    expect(mocks.list.mock.calls.some(([offset]) => offset === 40)).toBe(false);
    names = ["inserted", ...names];
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(container.textContent).toContain("inserted");
    expect(container.textContent).toContain("task-22");
    let finish!: (value: unknown) => void;
    mocks.list.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    mocks.taskId = "other";
    mocks.get.mockResolvedValue({ id: "other", name: "New detail", status: "deleted", schedule: {}, request_options: { attachments: [] } });
    await act(async () => root.render(createElement(ScheduledTasksApp, { active: true })));
    await act(async () => finish({ tasks: [{ id: "stale", name: "stale list" }] }));
    expect(container.textContent).toContain("New detail");
    expect(container.textContent).not.toContain("stale list");
  } finally {
    await act(async () => root.unmount());
  }
});
it("keeps the edited revision frozen when background refresh observes another writer", async () => {
  vi.useFakeTimers();
  const task: ScheduledTask = {
    notify_bound_im: false,
    id: "t",
    name: "Original",
    instruction: "hello",
    target_session_id: "s",
    revision: 1,
    schedule: { kind: "fixed_interval", interval_seconds: 3600 },
    request_options: { attachments: [], model: null, approval_mode: null },
    status: "active",
    next_fire_at_utc: null,
    pause_reason: "",
    needs_target: false,
    skill_id: null,
  };
  mocks.get.mockResolvedValue(task);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => {
      root.render(createElement(ScheduledTasksApp, { active: true }));
    });
    expect(container.textContent).toContain(
      "该任务上一次执行尚未结束，本次已跳过",
    );
    const edit = [...container.querySelectorAll("button")].find(
      (button) => button.getAttribute("aria-label") === "编辑",
    );
    expect(edit).toBeDefined();
    await act(async () => {
      edit!.click();
    });
    expect(mocks.editor.mock.lastCall?.[0].revision).toBe(1);
    mocks.get.mockResolvedValue({
      ...task,
      name: "Changed elsewhere",
      revision: 2,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.editor.mock.lastCall?.[0].revision).toBe(1);
  } finally {
    await act(async () => {
      root.unmount();
    });
  }
});
