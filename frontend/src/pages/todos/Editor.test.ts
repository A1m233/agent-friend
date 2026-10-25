// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TodoEditor } from "./Editor";
import type { Todo } from "@/services/api/todos";

const mocks = vi.hoisted(() => ({ update: vi.fn(), create: vi.fn(), get: vi.fn(), saveReminder: vi.fn(), saved: vi.fn(), preview: vi.fn() }));
vi.mock("@/components/scheduling/preview", () => ({ previewSchedule: mocks.preview }));
vi.mock("@/services/api/todos", () => ({ todosApi: mocks }));
vi.mock("@/services/http", () => ({ friendlyErrorMessage: (e: { friendly?: string; message?: string }) => e.friendly ?? e.message }));
let root: Root;
let host: HTMLDivElement;
const todo: Todo = { id: "t", title: "原标题", description: "原描述", status: "open", revision: 1,
  createdAt: "2030-10-24T01:00:00Z", updatedAt: "2030-10-24T01:00:00Z", reminders: [], lastOperation: null };
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2030, 9, 24, 12));
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn();
  });
  mocks.preview.mockResolvedValue(null);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });
async function fill(label: string, value: string) {
  if (label === "提醒时间") {
    const [date, time] = value.split("T");
    const [y, m, d] = date.split("-").map(Number);
    const [hour, minute] = time.split(":");
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="提醒时间"]')!.click());
    const day = [...document.querySelectorAll<HTMLButtonElement>('button[data-day]')].find(el => el.dataset.day === new Date(y, m - 1, d).toLocaleDateString())!;
    await act(async () => day.click());
    for (const [suffix, text] of [["小时", `${hour} 时`], ["分钟", `${minute} 分`]]) {
      await act(async () => document.querySelector(`[aria-label="提醒时间${suffix}"]`)!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
      await act(async () => [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(el => el.textContent === text)!.click());
    }
    await act(async () => [...document.querySelectorAll<HTMLButtonElement>('button')].find(el => el.textContent === "完成")!.click());
    return;
  }
  const element = document.querySelector(`[aria-label="${label}"]`) as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}
it("keeps draft and old revision until the user explicitly adopts the latest version", async () => {
  mocks.update.mockRejectedValueOnce({ code: "revision_conflict", friendly: "其他操作已修改" }).mockResolvedValue(todo);
  mocks.get.mockResolvedValue({ ...todo, title: "Agent 新标题", revision: 2 });
  const props = { target: { kind: "content" as const, todo }, onClose: vi.fn(), onSaved: mocks.saved };
  await act(async () => root.render(createElement(TodoEditor, props)));
  await fill("标题", "人工草稿");
  await act(async () => root.render(createElement(TodoEditor, { ...props, target: { kind: "content", todo: { ...todo, revision: 2 } } })));
  await submit();
  expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ revision: 1 }), "人工草稿", "原描述");
  expect((document.querySelector('[aria-label="标题"]') as HTMLInputElement).value).toBe("人工草稿");
  expect(document.body.textContent).toContain("Agent 新标题");
  await submit();
  expect(mocks.update).toHaveBeenCalledTimes(1);
  const adopt = [...document.querySelectorAll("button")].find((button) => button.textContent === "保留输入，采用最新版本")!;
  await act(async () => adopt.click());
  await submit();
  expect(mocks.update).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 2 }), "人工草稿", "原描述");
  expect(mocks.saved).toHaveBeenCalledWith("t");
});
it("hides the portal on workspace navigation and restores the draft on return", async () => {
  const props = { target: { kind: "content" as const, todo }, onClose: vi.fn(), onSaved: mocks.saved };
  await act(async () => root.render(createElement(TodoEditor, props)));
  await fill("标题", "保留草稿");
  await act(async () => root.render(createElement(TodoEditor, { ...props, active: false })));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await act(async () => root.render(createElement(TodoEditor, { ...props, active: true })));
  expect((document.querySelector('[aria-label="标题"]') as HTMLInputElement).value).toBe("保留草稿");
});
it("opens a blank reminder by default, requires its time, and preserves a failed create draft", async () => {
  mocks.create.mockRejectedValue({ friendly: "无法保存" });
  await act(async () => root.render(createElement(TodoEditor, { target: { kind: "create" }, onClose: vi.fn(), onSaved: mocks.saved })));
  await fill("标题", "新待办");
  const time = document.querySelector<HTMLButtonElement>('[aria-label="提醒时间"]')!;
  expect(time.textContent).toContain("选择提醒日期和时间");
  expect(document.querySelector('input[type="datetime-local"]')).toBeNull();
  for (const input of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input,textarea')) {
    expect(input.placeholder).not.toBe("");
  }
  expect(document.body.textContent).toContain("选择提醒日期和时间");
  expect(document.querySelector('[aria-label="时区"]')).toBeNull();
  expect(document.querySelector('[aria-label="夏令时重复时刻"]')).toBeNull();
  expect(document.body.textContent).not.toContain("初始");
  await submit();
  expect(mocks.create).not.toHaveBeenCalled();
  await fill("提醒时间", "2030-10-24T15:30");
  await submit();
  expect(mocks.create).toHaveBeenCalledWith("新待办", "", [{ kind: "once", local_datetime: "2030-10-24T15:30",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, fold: 0 }]);
  expect((document.querySelector('[aria-label="标题"]') as HTMLInputElement).value).toBe("新待办");
  expect(mocks.saved).not.toHaveBeenCalled();
  expect(document.body.textContent).toContain("无法保存");
});

it("allows removing the default reminder using a filled danger action", async () => {
  mocks.create.mockResolvedValue(todo);
  await act(async () => root.render(createElement(TodoEditor, { target: { kind: "create" }, onClose: vi.fn(), onSaved: mocks.saved })));
  await fill("标题", "不提醒");
  const remove = [...document.querySelectorAll("button")].find((button) => button.textContent === "移除提醒")!;
  expect(remove.dataset.variant).toBe("destructive");
  await act(async () => remove.click());
  expect(document.querySelector('[aria-label="提醒时间"]')).toBeNull();
  await submit();
  expect(mocks.create).toHaveBeenCalledWith("不提醒", "", []);
  expect(mocks.saved).toHaveBeenCalledWith(todo.id);
});

it("preserves an existing reminder timezone and DST fold without exposing editable controls", async () => {
  const reminder = { id: "r", todoId: todo.id, revision: 2, status: "active" as const,
    nextFireAtUtc: null, onceAtUtc: null, lastOccurrenceAtUtc: null,
    schedule: { kind: "once", local_datetime: "2030-11-03T01:30:45", timezone: "America/New_York", fold: 1 } };
  mocks.saveReminder.mockResolvedValue(reminder);
  await act(async () => root.render(createElement(TodoEditor, {
    target: { kind: "reminder", todo, reminder }, onClose: vi.fn(), onSaved: mocks.saved,
  })));
  expect(document.querySelector('[aria-label="时区"]')).toBeNull();
  expect(document.querySelector('[aria-label="夏令时重复时刻"]')).toBeNull();
  await fill("提醒时间", "2030-11-03T01:40:45");
  await submit();
  expect(mocks.saveReminder).toHaveBeenCalledWith(todo.id,
    { ...reminder.schedule, local_datetime: expect.stringMatching(/^2030-11-03T01:40:45(?:\.000)?$/) }, reminder);
});

it("keeps the remaining reminder's frequency when removing an earlier reminder", async () => {
  await act(async () => root.render(createElement(TodoEditor, { target: { kind: "create" }, onClose: vi.fn(), onSaved: mocks.saved })));
  await act(async () => [...document.querySelectorAll<HTMLButtonElement>('button')].find(el => el.textContent === "添加提醒")!.click());
  const second = document.querySelectorAll('[aria-label="提醒频率"]')[1];
  await act(async () => second.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  await act(async () => [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(el => el.textContent === "每周")!.click());
  await act(async () => [...document.querySelectorAll<HTMLButtonElement>('button')].find(el => el.textContent === "移除提醒")!.click());
  expect(document.querySelectorAll('[aria-label="提醒频率"]')).toHaveLength(1);
  expect(document.querySelector('[aria-label="提醒频率"]')?.textContent).toBe("每周");
  expect(document.querySelectorAll('[role="checkbox"]')).toHaveLength(7);
});
