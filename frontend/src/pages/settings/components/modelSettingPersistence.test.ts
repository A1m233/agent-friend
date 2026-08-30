import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/runtime-config", () => ({ persistBridgeRuntimeSetting: vi.fn() }));
vi.mock("@/lib/settings", () => ({ setSetting: vi.fn() }));
vi.mock("@/services", () => ({ modelsApi: { validate: vi.fn() } }));

import { persistBridgeRuntimeSetting } from "@/lib/runtime-config";
import { setSetting } from "@/lib/settings";
import { modelsApi } from "@/services";
import {
  NEW_CONVERSATION_DEFAULT_MODEL_COPY,
  persistModelSettingSelection,
} from "./modelSettingPersistence";

const mockPersistBridgeRuntimeSetting = vi.mocked(persistBridgeRuntimeSetting);
const mockSetSetting = vi.mocked(setSetting);
const mockValidate = vi.mocked(modelsApi.validate);

describe("persistModelSettingSelection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockValidate.mockResolvedValue({
      model: "kimi/kimi-k2.6",
      available: true,
      runtimeConfig: { model: "kimi/kimi-k2.6", reasoningPreset: "none" },
    });
    mockSetSetting.mockResolvedValue(undefined);
  });

  it("persists the new-conversation default without applying Bridge runtime settings", async () => {
    const selection = { provider: "kimi", model: "kimi/kimi-k2.6" } as const;

    const result = await persistModelSettingSelection("chatModel", selection);

    expect(mockValidate).toHaveBeenCalledWith(selection.model);
    expect(mockSetSetting).toHaveBeenCalledWith("chatModel", selection);
    expect(mockPersistBridgeRuntimeSetting).not.toHaveBeenCalled();
    expect(result).toEqual({ scope: "newConversationDraft" });
  });

  it("does not claim a restart or current-draft update in user-facing copy", () => {
    expect(NEW_CONVERSATION_DEFAULT_MODEL_COPY).toEqual({
      groupTitle: "新对话默认模型",
      groupDescription:
        "只决定之后新建对话的初始选择；当前对话和已经打开的新对话草稿不会跟着切换。",
      modelLabel: "默认模型",
      successTitle: "新对话默认模型已保存",
      successDescription:
        "下次新建对话时将默认选择该模型；当前对话和已打开的草稿不会切换。",
      errorTitle: "新对话默认模型未保存",
    });
    expect(JSON.stringify(NEW_CONVERSATION_DEFAULT_MODEL_COPY)).not.toContain("重启");
  });

  it("does not restart Bridge when persistence fails", async () => {
    mockSetSetting.mockRejectedValueOnce(new Error("disk full"));

    await expect(
      persistModelSettingSelection("chatModel", {
        provider: "kimi",
        model: "kimi/kimi-k2.6",
      }),
    ).rejects.toThrow("disk full");

    expect(mockPersistBridgeRuntimeSetting).not.toHaveBeenCalled();
  });

  it("keeps memory model on the Bridge restart apply path", async () => {
    const selection = {
      provider: "deepseek",
      model: "deepseek/deepseek-v4-pro",
    } as const;
    mockPersistBridgeRuntimeSetting.mockImplementation(async (persist) => {
      await persist();
      return { persisted: undefined, applied: true, applyError: null };
    });

    const result = await persistModelSettingSelection("memoryModel", selection);

    expect(mockSetSetting).toHaveBeenCalledWith("memoryModel", selection);
    expect(mockPersistBridgeRuntimeSetting).toHaveBeenCalledOnce();
    expect(result).toEqual({ scope: "bridge", applied: true, applyError: null });
  });
});
