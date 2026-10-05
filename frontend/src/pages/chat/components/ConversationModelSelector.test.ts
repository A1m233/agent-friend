// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setModelCatalogForTests, type ModelCatalog } from "@/lib/model-config";
import { useConversationStore } from "@/stores/conversation";
import { ConversationModelSelector } from "./ConversationModelSelector";
import { TooltipProvider } from "@/components/ui";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as typeof globalThis & { ResizeObserver: typeof ResizeObserver }).ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/settings/navigation", () => ({ openSettings: vi.fn() }));

const capabilities = {
  inputModalities: ["text" as const],
  acceptedMediaTypes: [],
  maxAttachmentBytes: 0,
  maxTotalAttachmentBytes: 0,
  maxAttachmentsPerTurn: 0,
  attachments: false,
  toolCalling: true,
  parallelToolCalls: true,
  structuredOutput: false,
  multimodalToolResults: false,
};

const catalog: ModelCatalog = {
  schemaVersion: 2,
  catalogRevision: "test",
  providers: [
    {
      id: "deepseek",
      label: "DeepSeek",
      credentialGroup: "deepseek",
      defaultChatModel: "deepseek/deepseek-v4-flash",
      defaultMemoryModel: "deepseek/deepseek-v4-flash",
      models: [
        {
          id: "deepseek/deepseek-v4-flash",
          label: "DeepSeek V4 Flash",
          routeId: "deepseek:flash",
          transportProfile: "openai-compatible",
          processingProfile: "text-only-v1",
          capabilities,
          reasoning: {
            control: "effort",
            defaultPreset: "off",
            presets: [
              { id: "off", label: "关闭" },
              { id: "high", label: "高" },
              { id: "max", label: "最高" },
            ],
          },
        },
        {
          id: "deepseek/deepseek-v4-flash-vision-exp",
          label: "DeepSeek V4 Flash Vision",
          routeId: "deepseek:vision",
          transportProfile: "openai-compatible",
          processingProfile: "tool-images-v1",
          capabilities,
          reasoning: {
            control: "effort",
            defaultPreset: "off",
            presets: [
              { id: "off", label: "关闭" },
              { id: "low", label: "低" },
              { id: "high", label: "高" },
              { id: "max", label: "最高" },
            ],
          },
        },
        {
          id: "deepseek/test-unsupported",
          label: "No Reasoning Model",
          routeId: "deepseek:test-unsupported",
          transportProfile: "openai-compatible",
          processingProfile: "text-only-v1",
          capabilities,
          reasoning: {
            control: "unsupported",
            defaultPreset: "none",
            presets: [{ id: "none", label: "不支持" }],
          },
        },
      ],
    },
  ],
};

function exactText(text: string): HTMLElement {
  const match = Array.from(document.querySelectorAll<HTMLElement>("body *")).find(
    (element) => element.textContent?.trim() === text,
  );
  if (!match) throw new Error(`missing text: ${text}`);
  return match;
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

async function moveMousePointer(element: Element): Promise<void> {
  const event = new MouseEvent("pointermove", { bubbles: true });
  Object.defineProperty(event, "pointerType", { value: "mouse" });
  await act(async () => {
    element.dispatchEvent(event);
    await new Promise((resolve) => window.setTimeout(resolve, 120));
  });
}

describe("ConversationModelSelector", () => {
  let container: HTMLDivElement;
  let root: Root;
  const selectRuntimeConfig = vi.fn(async (config: { model: string; reasoningPreset: string }) => ({
    status: "applied" as const,
    ...config,
  }));

  beforeEach(async () => {
    vi.clearAllMocks();
    setModelCatalogForTests(catalog);
    useConversationStore.setState({
      currentReasoningPreset: "high",
      modelSwitching: false,
      modelSwitchTarget: null,
      runCalibrationPhase: "ready",
      runSnapshots: {},
      historyLoading: false,
      selectRuntimeConfig,
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        createElement(
          TooltipProvider,
          null,
          createElement(ConversationModelSelector, {
            selectedModel: "deepseek/deepseek-v4-flash",
          }),
        ),
      );
    });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    setModelCatalogForTests(null);
  });

  it("keeps next-turn selection available while busy and displays the draft reasoning preset", async () => {
    await act(async () => {
      useConversationStore.setState({ runCalibrationPhase: "calibrating" });
      root.render(createElement(TooltipProvider, null, createElement(ConversationModelSelector, {
        selectedModel: "deepseek/deepseek-v4-flash", selectedReasoningPreset: "max",
      })));
    });
    const trigger = container.querySelector<HTMLButtonElement>("button")!;
    expect(trigger.disabled).toBe(false);
    expect(trigger.textContent).toContain("最高");
  });

  it("opens submenus on pointer movement and keeps the root open after concrete selections", async () => {
    const trigger = container.querySelector<HTMLButtonElement>("button");
    if (!trigger) throw new Error("missing trigger");
    await act(async () => {
      trigger.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    });
    expect(document.body.style.pointerEvents).not.toBe("none");

    const modelRow = exactText("模型").parentElement;
    if (!modelRow) throw new Error("missing model row");
    await moveMousePointer(modelRow);
    expect(exactText("DeepSeek V4 Flash Vision")).toBeTruthy();

    await act(async () => exactText("DeepSeek V4 Flash Vision").click());
    await settle();
    expect(selectRuntimeConfig).toHaveBeenCalledWith({
      model: "deepseek/deepseek-v4-flash-vision-exp",
      reasoningPreset: "off",
    });
    expect(exactText("模型")).toBeTruthy();
    expect(exactText("DeepSeek V4 Flash Vision")).toBeTruthy();
    expect(toast.success).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe(
      "下一条回复将使用 DeepSeek V4 Flash Vision · 关闭",
    );
    expect(document.body.textContent).not.toContain("已应用，下一条回复使用当前配置");

    const reasoningRow = exactText("推理强度").parentElement;
    if (!reasoningRow) throw new Error("missing reasoning row");
    await act(async () => {
      reasoningRow.focus();
    });
    await settle();
    await act(async () => exactText("最高").click());
    await settle();
    expect(selectRuntimeConfig).toHaveBeenCalledWith({
      model: "deepseek/deepseek-v4-flash",
      reasoningPreset: "max",
    });
    expect(exactText("推理强度")).toBeTruthy();
    expect(exactText("最高")).toBeTruthy();

    expect(toast.success).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe(
      "下一条回复将使用 DeepSeek V4 Flash · 最高",
    );

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.body.textContent).not.toContain("推理强度");
  });

  it("renders the Vision model with the DeepSeek effort presets", async () => {
    useConversationStore.setState({ currentReasoningPreset: "off" });
    await act(async () => {
      root.render(
        createElement(
          TooltipProvider,
          null,
          createElement(ConversationModelSelector, {
            selectedModel: "deepseek/deepseek-v4-flash-vision-exp",
          }),
        ),
      );
    });
    const trigger = container.querySelector<HTMLButtonElement>("button");
    if (!trigger) throw new Error("missing trigger");
    await act(async () => {
      trigger.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    });
    const reasoningRow = exactText("推理强度").parentElement;
    if (!reasoningRow) throw new Error("missing reasoning row");
    await act(async () => reasoningRow.focus());
    await settle();

    expect(exactText("最高")).toBeTruthy();
    expect(document.querySelectorAll('[role="menuitemradio"]')).toHaveLength(4);
    expect(document.body.textContent).not.toContain("不支持");
  });

  it("renders unsupported reasoning as read-only content instead of a false choice", async () => {
    useConversationStore.setState({ currentReasoningPreset: "none" });
    await act(async () => {
      root.render(
        createElement(
          TooltipProvider,
          null,
          createElement(ConversationModelSelector, {
            selectedModel: "deepseek/test-unsupported",
          }),
        ),
      );
    });
    const trigger = container.querySelector<HTMLButtonElement>("button");
    if (!trigger) throw new Error("missing trigger");
    await act(async () => {
      trigger.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
    });
    const reasoningRow = exactText("推理强度").parentElement;
    if (!reasoningRow) throw new Error("missing reasoning row");
    await act(async () => reasoningRow.focus());
    await settle();

    expect(exactText("不支持")).toBeTruthy();
    expect(document.querySelector('[role="menuitemradio"]')).toBeNull();
  });
});
