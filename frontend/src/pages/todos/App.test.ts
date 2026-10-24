// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TodosApp } from "./App";
import type { Todo } from "@/services/api/todos";

const mocks = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), transition: vi.fn(), removeReminder: vi.fn() }));
vi.mock("@/services/api/todos", () => ({ todosApi: mocks }));
vi.mock("./History", () => ({ History: () => null }));
vi.mock("./Editor", () => ({ TodoEditor: () => null }));
vi.mock("@/services/http", () => ({ friendlyErrorMessage: (e: { friendly: string }) => e.friendly }));
const todo: Todo = { id: "t", title: "周报", description: "提交附件", status: "open", revision: 3,
  createdAt: "2030-10-24T01:00:00Z", updatedAt: "2030-10-24T01:00:00Z", reminders: [],
  lastOperation: { id: 2, action: "updated", actor: "agent", session_id: "s", occurred_at: "2030-10-24T01:00:00Z" } };
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  mocks.list.mockResolvedValue({ todos: [todo] }); mocks.get.mockResolvedValue(todo);
  mocks.transition.mockResolvedValue(todo);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });
async function settle(ms = 30) { await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); }); }
async function click(text: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent?.includes(text));
  expect(button).toBeTruthy(); await act(async () => button!.click()); await settle();
}
it("loads only while active, searches the server and shows operation attribution", async () => {
  await act(async () => root.render(createElement(TodosApp, { active: false })));
  await settle(); expect(mocks.list).not.toHaveBeenCalled();
  await act(async () => root.render(createElement(TodosApp, { active: true })));
  await settle();
  expect(mocks.list).toHaveBeenCalledWith("open", "", 0, 50);
  expect(host.textContent).toContain("Agent · 编辑内容");
  const input = host.querySelector('[aria-label="搜索待办"]') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "附件");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle(300);
  expect(mocks.list).toHaveBeenLastCalledWith("open", "附件", 0, 50);
});
it("passes current revision for completion and preserves conflict feedback after refresh", async () => {
  await act(async () => root.render(createElement(TodosApp, { active: true })));
  await settle(); await click("周报");
  mocks.transition.mockRejectedValueOnce({ code: "revision_conflict", friendly: "其他操作已修改，请刷新" });
  await click("标记完成");
  expect(mocks.transition).toHaveBeenCalledWith(todo, "complete");
  expect(host.textContent).toContain("其他操作已修改，请刷新");
  await click("标记完成");
  expect(host.textContent).toContain("已完成待办，相关提醒已停用");
});

it("selects details without reloading the list and keeps creation time visible during refresh", async () => {
  let resolveDetail: (value: Todo) => void = () => {};
  mocks.get.mockImplementation(() => new Promise<Todo>((resolve) => { resolveDetail = resolve; }));
  await act(async () => root.render(createElement(TodosApp, { active: true })));
  await settle();
  const listCalls = mocks.list.mock.calls.length;
  await click("周报");
  expect(mocks.list).toHaveBeenCalledTimes(listCalls);
  expect(mocks.get).toHaveBeenCalledWith("t");
  const card = host.querySelector<HTMLButtonElement>('[aria-pressed="true"]')!;
  expect(card).not.toBeNull();
  expect(card.querySelector("time")?.dateTime).toBe(todo.createdAt);
  expect(host.querySelector('[aria-label="待办详情"] time')?.getAttribute("datetime")).toBe(todo.createdAt);
  await act(async () => resolveDetail(todo));
  let resolveList: (value: { todos: Todo[] }) => void = () => {};
  mocks.list.mockImplementationOnce(() => new Promise((resolve) => { resolveList = resolve; }));
  const refresh = host.querySelector<HTMLButtonElement>('[aria-label="刷新待办"]')!;
  await act(async () => refresh.click());
  await settle();
  expect(host.querySelector('[aria-label="待办条目"] [aria-pressed="true"]')).toBe(card);
  expect(host.querySelector('[aria-label="待办条目"]')!.textContent).not.toContain("正在刷新");
  expect(host.querySelector('[aria-label="待办列表"]')?.getAttribute("aria-busy")).toBe("true");
  await act(async () => resolveList({ todos: [todo] }));
});

it("ignores a late detail response from a previously selected todo", async () => {
  const second = { ...todo, id: "second", title: "出行清单" };
  mocks.list.mockResolvedValue({ todos: [todo, second] });
  let firstDetail: (value: Todo) => void = () => {};
  mocks.get.mockImplementation((id: string) => id === "t"
    ? new Promise<Todo>((resolve) => { firstDetail = resolve; }) : Promise.resolve(second));
  await act(async () => root.render(createElement(TodosApp, { active: true })));
  await settle(); await click("周报"); await click("出行清单");
  await act(async () => firstDetail(todo));
  expect(host.querySelector('[aria-label="待办详情"] h2')!.textContent).toBe("出行清单");
});
