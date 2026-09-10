// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CustomConnection } from "@/lib/settings";

const mock = vi.hoisted(() => ({
  connections: [] as CustomConnection[],
  save: vi.fn(),
  test: vi.fn(),
  discover: vi.fn(),
  metadata: vi.fn(),
}));
vi.mock("@/lib/settings", () => ({
  useSetting: (key: string) => [
    key === "modelConnections"
      ? { revision: 4, connections: mock.connections }
      : { provider: "deepseek", model: "deepseek/model" },
  ],
}));
vi.mock("@/lib/model-config", () => ({
  useModelCatalogStore: (select: (value: unknown) => unknown) =>
    select({ catalog: { providers: [] } }),
}));
vi.mock("@/lib/model-config/connections", () => ({
  saveConnections: mock.save,
  probeModel: mock.test,
  readModelMetadata: mock.metadata,
  retryConnections: vi.fn(),
  discoverModels: mock.discover,
  customModelReference: (c: string, m: string) => `custom/${c}/${m}`,
}));
import { CustomConnections } from "./CustomConnections";

let root: Root;
let host: HTMLDivElement;
async function click(text: string) {
  const element = Array.from(document.querySelectorAll("button")).find(
    (button) => button.textContent === text,
  );
  if (!element) throw new Error(`missing button ${text}`);
  await act(async () => element.click());
}
async function input(id: string, value: string) {
  const element = document.getElementById(id) as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mock.connections = [];
  mock.save.mockReset().mockResolvedValue({ persisted: true, applied: true });
  mock.test
    .mockReset()
    .mockResolvedValue({
      metadata: {
        id: "org/my-model",
        contextWindow: 8192,
        contextSource: "catalog",
        contextLabel: "目录参考",
      },
      generation: { status: "passed" },
      tools: { status: "passed" },
    });
  mock.discover
    .mockReset()
    .mockResolvedValue([
      {
        id: "org/my-model",
        contextWindow: 8192,
        contextSource: "endpoint",
        contextLabel: "服务返回",
      },
    ]);
  mock.metadata
    .mockReset()
    .mockResolvedValue({
      id: "unknown-model",
      contextWindow: null,
      contextSource: null,
      contextLabel: "未知",
    });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(createElement(CustomConnections)));
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function connectionFields() {
  await click("添加自定义连接");
  await input("connection-name", " My gateway ");
  await input("connection-url", "https://example.com/v1");
  await input("connection-key", "fixture-key");
}
async function chooseDiscovered() {
  await click("获取模型列表");
  await act(async () =>
    (document.querySelector('[role="checkbox"]') as HTMLButtonElement).click(),
  );
}
it("discovers and selects a model before saving without generating", async () => {
  await connectionFields();
  await chooseDiscovered();
  expect(mock.discover).toHaveBeenCalledWith(
    expect.objectContaining({ baseUrl: "https://example.com/v1", models: [] }),
    "fixture-key",
  );
  await click("保存");
  const [revision, connections, keys] = mock.save.mock.calls[0];
  expect(revision).toBe(4);
  expect(connections[0]).toMatchObject({
    name: "My gateway",
    protocol: "chat_completions",
    models: [{ name: "org/my-model", contextWindow: 8192 }],
  });
  expect(keys[connections[0].id]).toBe("fixture-key");
  expect(mock.test).not.toHaveBeenCalled();
  expect(document.querySelector('input[type="password"]')).toBeNull();
});
it("keeps manual entry available after discovery fails, then preserves a failed save draft", async () => {
  mock.discover.mockRejectedValueOnce(new Error("model_list_unavailable"));
  mock.save.mockRejectedValueOnce(
    new Error("settings_changed_reload_required"),
  );
  await connectionFields();
  await click("获取模型列表");
  await click("手动添加模型");
  const name = document.querySelector(
    'input[aria-label="Model Name"]',
  ) as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(name, "unknown-model");
    name.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click("添加此模型");
  await input(
    document.querySelector<HTMLInputElement>('input[id^="context-"]')!.id,
    "8192",
  );
  await click("保存");
  expect(
    (document.getElementById("connection-name") as HTMLInputElement).value,
  ).toBe(" My gateway ");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain(
    "其他窗口",
  );
});
it("tests the draft and invalidates results and automatic context when its endpoint changes", async () => {
  await connectionFields();
  await chooseDiscovered();
  await click("测试模型");
  expect(mock.save).not.toHaveBeenCalled();
  expect(document.querySelector('[role="status"]')?.textContent).toContain(
    "工具调用与回传：通过",
  );
  await input("connection-url", "https://other.example/v1");
  expect(document.body.textContent).not.toContain("模型生成：通过");
  expect(
    (document.querySelector('input[id^="context-"]') as HTMLInputElement).value,
  ).toBe("");
  const remove = document.querySelector(
    'button[aria-label="移除模型 org/my-model"]',
  )!;
  expect(remove.getAttribute("data-variant")).toBe("destructive");
  await act(async () => (remove as HTMLButtonElement).click());
  expect(document.body.textContent).toContain("已添加模型 · 0");
});

it("uses the scrollable modal layer and still closes with Escape", async () => {
  await click("添加自定义连接");
  const dialog = document.querySelector('[role="dialog"]')!;
  const overlay = document.querySelector('[data-slot="dialog-overlay"]')!;
  expect(overlay.contains(dialog)).toBe(true);
  expect(document.body.hasAttribute("data-scroll-locked")).toBe(true);
  expect(dialog.contains(document.activeElement)).toBe(true);
  await act(async () => {
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
  });
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.hasAttribute("data-scroll-locked")).toBe(false);
});


it("keeps operation feedback mounted when retrying a failed discovery", async () => {
  await click("添加自定义连接");
  await input("connection-url", "https://example.com/v1");
  await input("connection-key", "fixture-key");
  mock.discover.mockRejectedValueOnce(new Error("authentication_failed"));
  await click("获取模型列表");
  const feedback = document.querySelector('[role="alert"]');
  expect(feedback?.textContent).toContain("鉴权失败");
  let finish!: (value: unknown[]) => void;
  mock.discover.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  await click("获取模型列表");
  expect(feedback?.isConnected).toBe(true);
  expect(feedback?.getAttribute("role")).toBe("status");
  expect(feedback?.textContent).toContain("正在处理");
  await act(async () => finish([]));
  expect(feedback?.isConnected).toBe(true);
  expect(feedback?.textContent).toBe("");
});
