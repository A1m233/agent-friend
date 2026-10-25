// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { History } from "./History";
const mocks = vi.hoisted(() => ({ history: vi.fn(), openSession: vi.fn(), navigate: vi.fn() }));
vi.mock("@/services/api/todos", () => ({ todosApi: mocks }));
vi.mock("@/stores/conversation", () => ({ useConversationStore: { getState: () => ({ openSession: mocks.openSession }) } }));
vi.mock("@/lib/workspace/navigation", () => ({ useWorkspaceNavigationStore: { getState: () => ({ navigate: mocks.navigate }) } }));
let root: Root;
let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); vi.clearAllMocks();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
it("renders field differences and handles a deleted source session without navigating", async () => {
  mocks.history.mockResolvedValue({ items: [{ id: 1, actor: "agent", action: "status_changed", occurred_at: "2030-10-24T01:00:00Z",
    session_id: "missing", before: { status: "open" }, after: { status: "completed" } }], nextCursor: null });
  mocks.openSession.mockResolvedValue(false);
  await act(async () => root.render(createElement(History, { todoId: "t", revision: 1 })));
  expect(host.textContent).toContain("修改前：待办中");
  expect(host.textContent).toContain("修改后：已完成");
  await act(async () => (host.querySelector("button") as HTMLButtonElement).click());
  expect(host.textContent).toContain("来源会话已不存在或暂时无法打开");
  expect(mocks.navigate).not.toHaveBeenCalled();
});
it("does not fabricate history for older records", async () => {
  mocks.history.mockResolvedValue({ items: [], nextCursor: null });
  await act(async () => root.render(createElement(History, { todoId: "t", revision: undefined })));
  expect(host.textContent).toContain("旧记录的操作来源未知");
});
