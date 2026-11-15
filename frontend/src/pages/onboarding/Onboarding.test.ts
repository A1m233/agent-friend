// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Onboarding from "./Onboarding";
import { Input } from "@/components/ui";
import { guidePrompt, missingStep, type SetupStatus } from "./setup";

const f = vi.hoisted(() => ({
  invoke: vi.fn(),
  completed: vi.fn(),
  start: vi.fn(),
  groups: [],
  connection: {},
  chat: { provider: "custom", model: "custom/test/chat", reasoningPreset: "effort:medium" },
  memory: { provider: "kimi", model: "kimi/memory", reasoningPreset: "on" },
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: f.invoke }));
vi.mock("@/lib/settings", () => ({
  setSetting: f.completed,
  useSetting: (key: string) => [
    key === "chatModel" ? f.chat : key === "memoryModel" ? f.memory : f.connection,
    f.completed,
  ],
}));
vi.mock("@/stores", () => ({
  useConnectionStore: () => "online",
  useRuntimeCredentialStore: Object.assign(() => f.groups, { getState: () => ({}) }),
}));
vi.mock("@/pages/settings/components/CustomConnections", () => ({ CustomConnections: () => null }));
vi.mock("@/pages/settings/components/RuntimeConfigPanel", () => ({ CredentialEditor: () => null }));
vi.mock("@/pages/settings/components/CompanionshipSetting", () => ({ CompanionshipSetting: () => null }));
vi.mock("@/pages/settings/components/NotificationSettings", () => ({ NotificationSettings: () => null }));
vi.mock("@/pages/settings/components/PersonaPanel", () => ({
  PersonaPanel: ({ active }: { active: boolean }) =>
    createElement("div", { hidden: !active }, createElement(Input, { "aria-label": "人格草稿" })),
}));
vi.mock("./SpeechInput", () => ({ SpeechInput: () => createElement(Input, { "aria-label": "语音草稿" }) }));

let container: HTMLDivElement;
let root: Root;
const heading = () => container.querySelector("h1")?.textContent;
const button = (name: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === name && !b.closest("[hidden]"));
async function click(name: string) {
  const control = button(name);
  if (!control) throw new Error("missing " + name);
  await act(async () => control.click());
}
let status: SetupStatus;
beforeEach(async () => {
  vi.clearAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  status = {
    chatModel: { ...f.chat, provider: "custom" },
    memoryModel: { ...f.memory, provider: "kimi" },
    chatReady: true,
    memoryReady: true,
    models: [
      { ...f.chat, provider: "custom", label: "我的连接" },
      { ...f.memory, provider: "kimi", label: "我的记忆模型" },
    ],
  };
  f.invoke.mockImplementation(async (name) => (name === "get_onboarding_status" ? status : undefined));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(createElement(Onboarding, { active: true, onStart: f.start })));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("first run", () => {
  it("shows the same agent instructions that the welcome page copies", async () => {
    vi.useFakeTimers();
    expect(container.querySelector("details")).toBeNull();
    expect([...container.querySelectorAll("p")].some((p) => p.textContent === guidePrompt)).toBe(true);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await click("复制提示词");
    expect(writeText).toHaveBeenCalledWith(guidePrompt);
    expect(button("复制提示词")?.querySelector("svg.text-success")).not.toBeNull();
    expect(container.querySelector('[role="status"].sr-only')?.textContent).toBe("已复制");
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(button("复制提示词")?.querySelector("svg.text-success")).toBeNull();
    writeText.mockRejectedValueOnce(new Error("clipboard unavailable"));
    await click("复制提示词");
    expect(container.querySelector('[role="status"]')?.textContent).toContain("手动复制");
  });
  it("holds the connection step without credentials and identifies a missing memory model", async () => {
    status.models = [];
    await click("一起设置");
    await click("继续");
    expect(heading()).toBe("连接模型服务");
    expect(f.completed).not.toHaveBeenCalled();
    expect(button("稍后设置")).toBeUndefined();
    expect(
      missingStep({ ...status, models: [{ ...status.chatModel, label: "chat" }], memoryReady: false }),
    ).toBe("memory");
  });
  it("can use one BYOK connection for both defaults before personalization", async () => {
    status.models = [status.models[0]];
    status.memoryReady = false;
    f.invoke.mockImplementation(async (name, payload) => {
      if (name === "get_onboarding_status") return status;
      if (name === "select_onboarding_model" && payload.purpose === "memory") {
        status.memoryModel = status.chatModel;
        status.memoryReady = true;
      }
    });
    await click("一起设置");
    await click("继续");
    await click("继续");
    expect(f.invoke).toHaveBeenCalledWith("select_onboarding_model", { purpose: "chat", model: f.chat });
    await click("沿用对话模型");
    expect(f.completed).not.toHaveBeenCalled();
    await click("继续");
    expect(heading()).toBe("这下可以开聊啦！");
    expect(f.completed).toHaveBeenCalledWith("onboardingCompleted", true);
    expect(f.start).not.toHaveBeenCalled();
    expect(f.invoke).toHaveBeenCalledWith("select_onboarding_model", {
      purpose: "memory",
      model: f.chat,
    });
  });
  it("offers back and continue through personalization and finishes only on the last page", async () => {
    await click("一起设置");
    await click("继续");
    await click("继续");
    await click("继续");
    const footerLabels = () =>
      [...container.querySelectorAll("footer button")].map((b) => b.textContent?.trim());
    for (const title of ["这下可以开聊啦！", "自主陪伴与通知", "和伙伴一起调整人格"]) {
      expect(heading()).toBe(title);
      expect(footerLabels()).toEqual(["上一步", "继续"]);
      await click("继续");
    }
    expect(heading()).toBe("用说话代替打字");
    expect(footerLabels()).toEqual(["上一步", "完成并开始使用"]);
    expect(f.start).not.toHaveBeenCalled();
    await click("完成并开始使用");
    expect(f.start).toHaveBeenCalledOnce();
  });
  it("enters ready only after both models are confirmed and the completion flag is saved", async () => {
    await click("一起设置");
    await click("继续");
    await click("继续");
    status.memoryReady = false;
    await click("继续");
    expect(heading()).toBe("选择默认记忆模型");
    expect(f.completed).not.toHaveBeenCalled();
    status.memoryReady = true;
    f.completed.mockRejectedValueOnce(new Error("无法保存设置"));
    await click("继续");
    expect(heading()).toBe("选择默认记忆模型");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("无法保存设置");
    await click("继续");
    expect(heading()).toBe("这下可以开聊啦！");
    expect(f.completed).toHaveBeenLastCalledWith("onboardingCompleted", true);
    expect(f.start).not.toHaveBeenCalled();
  });
  it("keeps drafts across back/skip and finishes only after the user chooses to leave", async () => {
    await click("一起设置");
    await click("继续");
    await click("继续");
    await click("继续");
    expect(f.invoke).toHaveBeenCalledWith("select_onboarding_model", {
      purpose: "memory",
      model: f.memory,
    });
    await click("继续");
    const field = (name: string) => container.querySelector<HTMLInputElement>('[aria-label="' + name + '"]')!;
    await click("继续");
    field("人格草稿").value = "保留候选";
    await click("继续");
    field("语音草稿").value = "draft";
    await click("上一步");
    expect(field("人格草稿").value).toBe("保留候选");
    await click("继续");
    expect(field("语音草稿").value).toBe("draft");
    await click("完成并开始使用");
    expect(f.start).toHaveBeenCalledOnce();
  });
});
