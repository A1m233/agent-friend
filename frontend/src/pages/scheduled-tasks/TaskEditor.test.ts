// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useModelCatalogStore, type ModelCatalog } from "@/lib/model-config";
import { TaskEditor } from "./TaskEditor";
import type { ScheduledTask } from "@/services/api/scheduledTasks";
import { toast } from "sonner";

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const mock = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), post: vi.fn(), rename: vi.fn(), saved: vi.fn() }));
vi.mock("@/lib/settings", () => ({
  useSetting: () => [{ model: "m", reasoningPreset: "low" }],
}));
vi.mock("@/services/http", () => ({
  http: { get: vi.fn(async () => ({ skills: [] })), post: mock.post },
  friendlyErrorMessage: (e: Error) => e.message,
}));
vi.mock("@/services/api/scheduledTasks", () => ({ scheduledTasksApi: { create: mock.create, update: mock.update } }));
vi.mock("@/services/api/sessions", () => ({ sessionsApi: {
  list: vi.fn(async () => [{ session_id: "s", title: "Existing", model: "m", reasoningPreset: "low" }]),
  rename: mock.rename,
} }));

let root: Root;
let host: HTMLDivElement;
const catalog = { providers: [{ models: [{ id: "m", label: "Model", reasoning: { control: "effort", defaultPreset: "low", allowCustom: true, presets: [{ id: "low", label: "低" }, { id: "high", label: "高" }] } }] }] } as ModelCatalog;
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Element.prototype.scrollIntoView = vi.fn();
  vi.clearAllMocks();
  useModelCatalogStore.setState({ catalog });
  vi.spyOn(useModelCatalogStore.getState(), "load").mockResolvedValue();
  mock.post.mockImplementation(async (url: string) => url === "/v1/sessions" ? { session_id: "new-id" } : { next_fire_at_utc: null });
  mock.create.mockResolvedValue({ id: "task" });
  mock.update.mockResolvedValue({ id: "task" });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(createElement(TaskEditor, { onSaved: mock.saved, onCancel: vi.fn() })));
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});
async function fill(label: string, value: string) {
  const element = [...host.querySelectorAll("label")].find((el) => el.textContent?.trim() === label)?.querySelector("input,textarea") as HTMLInputElement | HTMLTextAreaElement;
  expect(element, label).toBeTruthy();
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function click(text: string) {
  const element = [...document.querySelectorAll("button")].find((el) => el.textContent?.includes(text));
  expect(element, text).toBeTruthy();
  await act(async () => element!.click());
}
async function select(label: string, text: string) {
  const trigger = document.querySelector(`[role="combobox"][aria-label="${label}"]`)!;
  await act(async () => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  const option = [...document.querySelectorAll('[role="option"]')].find((el) => el.textContent === text) as HTMLElement;
  expect(option, text).toBeTruthy();
  await act(async () => option.click());
}
it("uses the independently confirmed session name and keeps default request config inherited", async () => {
  await fill("任务名称", "Task name");
  await fill("发送内容", "hello");
  await fill("新会话名称", "Conversation name");
  expect(document.querySelector('[aria-label="访问权限"]')).toBeNull();
  expect(document.querySelector('[aria-label="模型"]')).toBeNull();
  expect(document.querySelector('[aria-label="推理强度"]')).toBeNull();
  await click("更多设置");
  expect(document.querySelector('[aria-label="模型"]')?.textContent).toBe("Model");
  expect(document.querySelector('[aria-label="推理强度"]')?.textContent).toBe("低");
  expect([...host.querySelectorAll("label")].some((el) => el.textContent?.trim() === "时区")).toBe(false);
  await click("保存");
  expect(mock.post).toHaveBeenCalledWith("/v1/sessions", expect.objectContaining({ title: "Conversation name", model: "m", reasoningPreset: "low" }));
  expect(mock.create).toHaveBeenCalledWith(expect.objectContaining({ name: "Task name", target_session_id: "new-id", request_options: { model: null, approval_mode: null, attachments: [] } }));
  expect(mock.create.mock.lastCall?.[0].schedule.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
});
it("can change reasoning directly while following a session, then return to following", async () => {
  await fill("任务名称", "Task");
  await fill("发送内容", "hello");
  await select("目标会话", "Existing");
  expect(host.textContent).not.toContain("新会话名称");
  await click("更多设置");
  await select("推理强度", "高");
  await click("保存");
  expect(mock.create).toHaveBeenLastCalledWith(expect.objectContaining({ request_options: expect.objectContaining({ model: { model_id: "m", reasoning_preset: "high" } }) }));
  expect(mock.post.mock.calls.some(([url]) => url === "/v1/sessions")).toBe(false);
  await click("恢复会话配置");
  await click("保存");
  expect(mock.create).toHaveBeenLastCalledWith(expect.objectContaining({ request_options: expect.objectContaining({ model: null }) }));
});
it("does not create a session for an empty confirmed name", async () => {
  await fill("任务名称", "Task");
  await fill("发送内容", "hello");
  await fill("新会话名称", "   ");
  await click("保存");
  expect(host.textContent).toContain("请填写新会话名称");
  expect(mock.create).not.toHaveBeenCalled();
  expect(mock.post.mock.calls.some(([url]) => url === "/v1/sessions")).toBe(false);
});
it("requires a date before creating a session for a one-off task", async () => {
  await fill("任务名称", "Task");
  await fill("发送内容", "hello");
  await select("执行频率", "仅一次");
  expect(host.textContent).toContain("请选择执行日期和时间");
  await click("保存");
  expect(mock.create).not.toHaveBeenCalled();
  expect(mock.post.mock.calls.some(([url]) => url === "/v1/sessions")).toBe(false);
});
it("keeps custom effort collapsed until requested and saves it for the selected model", async () => {
  await fill("任务名称", "Task");
  await fill("发送内容", "hello");
  expect(host.querySelector('[aria-label="自定义推理强度"]')).toBeNull();
  await click("更多设置");
  await click("自定义强度");
  const input = host.querySelector('[aria-label="自定义推理强度"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "xhigh");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("使用此强度");
  await click("保存");
  expect(mock.create).toHaveBeenLastCalledWith(expect.objectContaining({ request_options: expect.objectContaining({ model: { model_id: "m", reasoning_preset: "effort:xhigh" } }) }));
});
it("has no synthetic follow option in model or reasoning menus", async () => {
  await click("更多设置");
  for (const label of ["模型", "推理强度"]) {
    const trigger = host.querySelector(`[role="combobox"][aria-label="${label}"]`)!;
    await act(async () => trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    const options = [...document.querySelectorAll('[role="option"]')];
    expect(options.some((option) => /跟随|默认/.test(option.textContent ?? ""))).toBe(false);
    await act(async () => (options[0] as HTMLElement).click());
  }
});
it("reuses the created session and updates its name when retrying a failed task save", async () => {
  mock.create.mockRejectedValueOnce(new Error("offline"));
  await fill("任务名称", "Task");
  await fill("发送内容", "hello");
  await click("保存");
  await fill("新会话名称", "Corrected name");
  await click("保存");
  expect(mock.post.mock.calls.filter(([url]) => url === "/v1/sessions")).toHaveLength(1);
  expect(mock.rename).toHaveBeenCalledWith("new-id", "Corrected name");
  expect(mock.saved).toHaveBeenCalledTimes(1);
});

it.each([
  ["每天", { kind: "recurring", frequency: "daily", interval: 1, local_time: "16:37:00", timezone: "Asia/Shanghai", starts_on: "2030-01-01", weekdays: [] }],
  ["每周", { kind: "recurring", frequency: "weekly", interval: 1, local_time: "16:37:00", timezone: "Asia/Shanghai", starts_on: "2030-01-01", weekdays: [1, 3, 7] }],
  ["工作日", { kind: "recurring", frequency: "weekly", interval: 1, local_time: "16:37:00", timezone: "Asia/Shanghai", starts_on: "2030-01-01", weekdays: [1, 2, 3, 4, 5] }],
  ["仅一次", { kind: "once", local_datetime: "2030-01-02T03:04:00", timezone: "Asia/Shanghai" }],
] as const)("prefills %s and does not reschedule when renaming", async (label, schedule) => {
  const task = {
    id: "edited", name: "Before", instruction: "hello", target_session_id: "s",
    schedule, revision: 1, request_options: { attachments: [], model: null, approval_mode: null },
    status: "active", next_fire_at_utc: null, pause_reason: "", needs_target: false, skill_id: null,
  } as unknown as ScheduledTask;
  await act(async () => root.render(createElement(TaskEditor, { key: "edit", task, onSaved: mock.saved, onCancel: vi.fn() })));
  expect(host.querySelector('[aria-label="执行频率"]')?.textContent).toBe(label);
  if (label === "仅一次") {
    expect(host.querySelector('[aria-label="执行日期和时间"]')?.textContent).toBe("2030-01-02 03:04");
  } else {
    expect(host.querySelector('[aria-label="执行小时"]')?.textContent).toBe("16 时");
    expect(host.querySelector('[aria-label="执行分钟"]')?.textContent).toBe("37 分");
  }
  if (label === "每周") expect([...host.querySelectorAll('[role="checkbox"][data-state="checked"]')].map((el) => el.parentElement?.textContent)).toEqual(["周一", "周三", "周日"]);
  await fill("任务名称", "After");
  await click("保存");
  expect(mock.update).toHaveBeenCalledWith(task, expect.objectContaining({ name: "After", schedule: undefined }));
  expect(toast.success).toHaveBeenCalledExactlyOnceWith("定时任务已保存");
  expect(host.textContent).not.toContain("保持现有时间安排");
});

it("does not show success when updating a task fails", async () => {
  const task = {
    id: "edited", name: "Before", instruction: "hello", target_session_id: "s",
    schedule: { kind: "recurring", frequency: "daily", interval: 1, local_time: "09:00:00", timezone: "Asia/Shanghai", starts_on: "2030-01-01", weekdays: [] },
    revision: 1, request_options: { attachments: [], model: null, approval_mode: null },
    status: "active", next_fire_at_utc: null, pause_reason: "", needs_target: false, skill_id: null,
  } as ScheduledTask;
  mock.update.mockRejectedValueOnce(new Error("保存失败"));
  await act(async () => root.render(createElement(TaskEditor, { key: "edit", task, onSaved: mock.saved, onCancel: vi.fn() })));
  await click("保存");
  expect(host.textContent).toContain("保存失败");
  expect(mock.saved).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});
