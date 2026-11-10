// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { commandRuntimeApi, type CommandRuntimeStatus } from "@/services/api/commandRuntime";
import { useCommandRuntimeStore } from "@/stores/commandRuntime";
import { PermissionPanel } from "./PermissionPanel";

vi.mock("@/stores", () => import("@/stores/commandRuntime"));
vi.mock("@/lib/permissions", () => ({
  usePermissionSnapshot: () => ({ items: [], activeFlow: null, refreshing: false }),
  refreshPermissions: vi.fn().mockResolvedValue({ items: [], activeFlow: null, refreshing: false }),
}));

const repairRequired: CommandRuntimeStatus = {
  platform: "windows", available: true, windows_sandbox_ready: false,
  requires_windows_sandbox_setup: true,
  sandbox_capability: { state: "repair_required", supported: true, ready: false, version: 1 },
};
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  useCommandRuntimeStore.getState().clear();
  vi.spyOn(commandRuntimeApi, "status").mockResolvedValue(repairRequired);
  container = document.createElement("div");
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function button(label: string) {
  const match = Array.from(container.querySelectorAll("button")).find((el) => el.textContent === label);
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

it("can start sandbox repair even when the global approval query returns 422", async () => {
  const pending = vi.spyOn(commandRuntimeApi, "pending").mockRejectedValue({ status: 422 });
  vi.spyOn(commandRuntimeApi, "processes").mockResolvedValue([]);
  const setup = vi.spyOn(commandRuntimeApi, "setupWindowsSandbox").mockResolvedValue(repairRequired);
  await expect(useCommandRuntimeStore.getState().refresh()).rejects.toMatchObject({ status: 422 });
  await act(async () => root.render(createElement(PermissionPanel)));
  expect(container.textContent).toContain("需要修复");
  expect(button("开始准备").disabled).toBe(false);
  await act(async () => button("开始准备").click());
  expect(setup).toHaveBeenCalledOnce();
  expect(pending).toHaveBeenCalledOnce();
});

it("shows a failed status read and recovers on recheck", async () => {
  vi.mocked(commandRuntimeApi.status).mockRejectedValueOnce(new Error("offline"));
  await act(async () => root.render(createElement(PermissionPanel)));
  expect(container.textContent).toContain("读取失败，可重新检测");
  expect(container.textContent).not.toContain("开始准备");
  await act(async () => button("重新检测").click());
  expect(container.textContent).not.toContain("读取失败");
  expect(button("开始准备").disabled).toBe(false);
});
