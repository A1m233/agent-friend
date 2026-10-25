import { invoke } from "@tauri-apps/api/core";
import { http } from "@/services/http";
import { ensureLocalServiceReady } from "@/services/runtime";
import { isTauri } from "@/utils/tauri";

export type TodoStatus = "open" | "completed" | "cancelled";
export interface TodoOperation {
  id: number;
  action: string;
  actor: "user" | "agent" | "system" | "unknown";
  session_id: string | null;
  occurred_at: string;
}
export interface TodoHistory extends TodoOperation {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}
export interface TodoReminder {
  id: string;
  todoId: string;
  schedule: Record<string, unknown>;
  status: "active" | "disabled" | "exhausted";
  nextFireAtUtc: string | null;
  onceAtUtc: string | null;
  lastOccurrenceAtUtc: string | null;
  revision: number;
}
export interface Todo {
  id: string;
  title: string;
  description: string;
  status: TodoStatus;
  revision: number;
  createdAt: string;
  updatedAt: string;
  reminders: TodoReminder[];
  lastOperation: TodoOperation | null;
}
export type TodoAction = "create" | "update" | "complete" | "cancel" | "reopen"
  | "add_reminder" | "update_reminder" | "remove_reminder";

async function mutate<T>(action: TodoAction, payload: Record<string, unknown>, todoId?: string, reminderId?: string): Promise<T> {
  if (!isTauri()) throw { friendly: "请在桌面端管理待办。", code: "desktop_required" };
  await ensureLocalServiceReady("bridge");
  let result: T & { error?: { code: string; message: string } };
  try {
    result = await invoke("mutate_todo", { request: { action, payload, todo_id: todoId, reminder_id: reminderId } });
  } catch {
    throw { friendly: "未能确认保存结果，请刷新查看后再操作。", code: "todo_transport" };
  }
  if (result.error) throw { friendly: result.error.message, code: result.error.code };
  return result;
}

export const todosApi = {
  list: (status: string, search: string, offset = 0, limit = 50) =>
    http.get<never, { todos: Todo[] }>("/v1/todos", { params: { status, search, offset, limit } }),
  get: (id: string) => http.get<never, Todo>(`/v1/todos/${encodeURIComponent(id)}`),
  history: (id: string, beforeId?: number) => http.get<never, { items: TodoHistory[]; nextCursor: number | null }>(
    `/v1/todos/${encodeURIComponent(id)}/history`, { params: { before_id: beforeId, limit: 30 } }),
  create: (title: string, description: string, reminders: Record<string, unknown>[]) =>
    mutate<Todo>("create", { title, description, reminders }),
  update: (todo: Todo, title: string, description: string) =>
    mutate<Todo>("update", { revision: todo.revision, title, description }, todo.id),
  transition: (todo: Todo, action: "complete" | "cancel" | "reopen") =>
    mutate<Todo>(action, { revision: todo.revision }, todo.id),
  saveReminder: (todoId: string, schedule: Record<string, unknown>, reminder?: TodoReminder, enabled = true) =>
    reminder ? mutate<TodoReminder>("update_reminder", { revision: reminder.revision, schedule, enabled }, todoId, reminder.id)
      : mutate<TodoReminder>("add_reminder", { schedule }, todoId),
  removeReminder: (todoId: string, reminder: TodoReminder) =>
    mutate<TodoReminder>("remove_reminder", { revision: reminder.revision }, todoId, reminder.id),
};
