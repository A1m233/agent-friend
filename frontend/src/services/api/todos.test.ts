import { beforeEach, expect, it, vi } from "vitest";
import { todosApi, type Todo } from "./todos";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), ready: vi.fn(), isTauri: vi.fn(), get: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@/services/runtime", () => ({ ensureLocalServiceReady: mocks.ready }));
vi.mock("@/utils/tauri", () => ({ isTauri: mocks.isTauri }));
vi.mock("@/services/http", () => ({ http: { get: mocks.get } }));
beforeEach(() => { vi.clearAllMocks(); mocks.isTauri.mockReturnValue(true); mocks.invoke.mockResolvedValue({ id: "todo" }); });

it("routes mutations through native command without caller-supplied actor or token", async () => {
  await todosApi.create("标题", "描述", []);
  expect(mocks.ready).toHaveBeenCalledWith("bridge");
  expect(mocks.invoke).toHaveBeenCalledWith("mutate_todo", { request: {
    action: "create", payload: { title: "标题", description: "描述", reminders: [] }, todo_id: undefined, reminder_id: undefined,
  } });
});
it("preserves revision conflicts for the form and refuses web attribution", async () => {
  mocks.invoke.mockResolvedValue({ error: { code: "revision_conflict", message: "请刷新" } });
  await expect(todosApi.transition({ id: "t", revision: 5 } as Todo, "complete")).rejects.toEqual({ code: "revision_conflict", friendly: "请刷新" });
  mocks.isTauri.mockReturnValue(false);
  await expect(todosApi.create("t", "", [])).rejects.toMatchObject({ code: "desktop_required" });
  expect(mocks.invoke).toHaveBeenCalledTimes(1);
});
it("does not retry an uncertain transport outcome", async () => {
  mocks.invoke.mockRejectedValue("network");
  await expect(todosApi.create("t", "", [])).rejects.toMatchObject({ code: "todo_transport" });
  expect(mocks.invoke).toHaveBeenCalledTimes(1);
});
