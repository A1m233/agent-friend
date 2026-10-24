// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TodoEditor } from "./Editor";
import type { Todo } from "@/services/api/todos";

const mocks = vi.hoisted(() => ({ update: vi.fn(), create: vi.fn(), get: vi.fn(), saveReminder: vi.fn(), saved: vi.fn() }));
vi.mock("@/services/api/todos", () => ({ todosApi: mocks }));
vi.mock("@/services/http", () => ({ friendlyErrorMessage: (e: { friendly?: string; message?: string }) => e.friendly ?? e.message }));
let root: Root;
let host: HTMLDivElement;
const todo: Todo = { id: "t", title: "原标题", description: "原描述", status: "open", revision: 1,
  createdAt: "2030-10-24T01:00:00Z", updatedAt: "2030-10-24T01:00:00Z", reminders: [], lastOperation: null };
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });
async function fill(label: string, value: string) {
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
it("keeps a failed create draft and submits initial reminders with the create", async () => {
  mocks.create.mockRejectedValue({ friendly: "无法保存" });
  await act(async () => root.render(createElement(TodoEditor, { target: { kind: "create" }, onClose: vi.fn(), onSaved: mocks.saved })));
  await fill("标题", "新待办");
  await submit();
  expect(mocks.create).toHaveBeenCalledWith("新待办", "", []);
  expect((document.querySelector('[aria-label="标题"]') as HTMLInputElement).value).toBe("新待办");
  expect(mocks.saved).not.toHaveBeenCalled();
  expect(document.body.textContent).toContain("无法保存");
});
