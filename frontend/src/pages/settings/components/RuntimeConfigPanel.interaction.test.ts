// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeCredentialGroupSnapshot } from "@/lib/runtime-config";

const state = vi.hoisted(() => ({
  groups: [] as RuntimeCredentialGroupSnapshot[], loading: false, error: null,
  refresh: vi.fn(), applyGroup: vi.fn(), confirmSave: vi.fn(), cancelOperation: vi.fn(),
  retryApply: vi.fn(), retryValidation: vi.fn(),
}));
vi.mock("@/stores", () => ({
  startRuntimeCredentialSync: () => () => undefined,
  useRuntimeCredentialStore: Object.assign(
    (selector: (value: typeof state) => unknown) => selector(state),
    { getState: () => state },
  ),
}));
vi.mock("@/lib/settings", () => ({
  useSetting: () => [{ provider: "deepseek", model: "deepseek-chat" }],
}));
vi.mock("@/lib/model-config", () => ({
  useModelCatalogStore: (selector: (value: unknown) => unknown) => selector({ catalog: {providers: []}, load: async () => undefined }),
  modelProvidersFromCatalog: () => [], providerModelsFromCatalog: () => [],
  credentialGroupForModel: () => "deepseek", defaultModelSelection: () => undefined,
}));
import { RuntimeConfigPanel } from "./RuntimeConfigPanel";

let container: HTMLDivElement;
let root: Root;
const click = async (element: Element | null) => {
  if (!element) throw new Error("Missing interaction target");
  await act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
};
const button = (label: string) => container.querySelector(`[aria-label="${label}"]`);

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Element.prototype.scrollIntoView = vi.fn();
  state.groups = [{
    group: "deepseek", items: [{ name: "DEEPSEEK_API_KEY", kind: "secret", group: "deepseek", configured: false, effect: "restartBridge" }],
    configured: false, partiallyConfigured: false, missingRequiredFields: ["DEEPSEEK_API_KEY"],
    validation: "none", effect: "restartBridge",
    operation: { phase: "idle", persisted: false, applied: false },
  }];
  state.applyGroup.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(createElement(RuntimeConfigPanel)));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("runtime credential navigation", () => {
  it("preserves a credential draft across collapse and tab changes and saves through the existing API", async () => {
    await click(button("编辑DeepSeek凭据"));
    const input = container.querySelector<HTMLInputElement>('input[aria-label="DeepSeek API Key 新值"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "test-key");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(button("收起DeepSeek凭据"));
    expect(input.closest("[data-slot=collapsible-content]")?.hasAttribute("hidden")).toBe(true);
    const tabs = container.querySelectorAll<HTMLElement>('[role="tab"]');
    await act(async () => tabs[1].focus());
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    await act(async () => tabs[0].focus());
    await click(button("编辑DeepSeek凭据"));
    expect(input.value).toBe("test-key");
    state.applyGroup.mockResolvedValue(state.groups[0]);
    await click([...container.querySelectorAll("#runtime-credential-deepseek button")].find((el) => el.textContent?.includes("保存此组"))!);
    expect(state.applyGroup).toHaveBeenCalledWith("deepseek", [{ name: "DEEPSEEK_API_KEY", action: "set", value: "test-key" }]);
  });

  it("routes external service and model credential links to the right tab and expands the editor", async () => {
    await act(async () => root.render(createElement(RuntimeConfigPanel, { focusGroup: "qWeather" })));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("服务凭据");
    expect(button("收起天气凭据")?.getAttribute("aria-expanded")).toBe("true");
    await act(async () => root.render(createElement(RuntimeConfigPanel, { focusGroup: "kimi" })));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("模型");
    expect(button("收起Kimi凭据")?.getAttribute("aria-expanded")).toBe("true");
  });

  it.each(["validating", "awaitingConfirmation"] as const)("keeps %s reachable by locking collapse and tab switching", async (phase) => {
    await click(button("编辑DeepSeek凭据"));
    state.groups = [{ ...state.groups[0], operation: { phase, persisted: false, applied: false } }];
    await act(async () => root.render(createElement(RuntimeConfigPanel)));
    expect((button("收起DeepSeek凭据") as HTMLButtonElement).disabled).toBe(true);
    expect([...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].every((el) => el.disabled)).toBe(true);
  });
});
