import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  credentialGroupForModel,
  defaultModelRuntimeConfig,
  defaultModelSelection,
  modelOption,
  modelProviders,
  modelProvidersFromCatalog,
  normalizeModelSelection,
  providerModelsFromCatalog,
  modelRuntimeConfigLabel,
  resolveDefaultConversationModel,
  setModelCatalogForTests,
  type ModelCatalog,
} from "./index";

const catalog: ModelCatalog = {
  schemaVersion: 1,
  catalogRevision: "test",
  providers: [
    {
      id: "deepseek",
      label: "DeepSeek",
      credentialGroup: "deepseek",
      defaultChatModel: "deepseek/chat",
      defaultMemoryModel: "deepseek/memory",
      models: [
        {
          id: "deepseek/chat",
          label: "chat",
          routeId: "deepseek:chat",
          transportProfile: "openai-compatible",
          processingProfile: "text-only-v1",
          capabilities: {
            inputModalities: ["text"],
            acceptedMediaTypes: [],
            maxAttachmentBytes: 0,
            maxTotalAttachmentBytes: 0,
            maxAttachmentsPerTurn: 0,
            attachments: false,
            toolCalling: true,
            parallelToolCalls: true,
            structuredOutput: false,
            multimodalToolResults: false,
            reasoningReplay: false,
          },
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
          id: "deepseek/memory",
          label: "memory",
          routeId: "deepseek:memory",
          transportProfile: "openai-compatible",
          processingProfile: "text-only-v1",
          capabilities: {
            inputModalities: ["text"],
            acceptedMediaTypes: [],
            maxAttachmentBytes: 0,
            maxTotalAttachmentBytes: 0,
            maxAttachmentsPerTurn: 0,
            attachments: false,
            toolCalling: true,
            parallelToolCalls: true,
            structuredOutput: false,
            multimodalToolResults: false,
            reasoningReplay: false,
          },
        },
        {
          id: "deepseek/deepseek-v4-flash-vision-exp",
          label: "DeepSeek V4 Flash Vision",
          routeId: "deepseek:vision",
          transportProfile: "openai-compatible",
          processingProfile: "deepseek-vision-files-v1",
          capabilities: {
            inputModalities: ["text", "image"],
            acceptedMediaTypes: ["image/jpeg", "image/png", "image/webp"],
            maxAttachmentBytes: 64 * 1024 * 1024,
            maxTotalAttachmentBytes: 100 * 1024 * 1024,
            maxAttachmentsPerTurn: 10,
            attachments: true,
            toolCalling: true,
            parallelToolCalls: true,
            structuredOutput: false,
            multimodalToolResults: false,
            reasoningReplay: false,
          },
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
      ],
    },
    {
      id: "kimi",
      label: "Kimi",
      credentialGroup: "kimi",
      defaultChatModel: "kimi/vision",
      defaultMemoryModel: "kimi/vision",
      models: [{
        id: "kimi/vision",
        label: "vision",
        routeId: "kimi:vision",
        transportProfile: "openai-compatible",
        processingProfile: "kimi-multimodal-v1",
        capabilities: {
          inputModalities: ["text", "image", "document"],
          acceptedMediaTypes: ["image/png", "application/pdf", "text/plain"],
          maxAttachmentBytes: 100,
          maxTotalAttachmentBytes: 100,
          maxAttachmentsPerTurn: 10,
          attachments: true,
          toolCalling: true,
          parallelToolCalls: true,
          structuredOutput: false,
          multimodalToolResults: false,
          reasoningReplay: false,
        },
        reasoning: {
          control: "toggle",
          defaultPreset: "on",
          presets: [
            { id: "off", label: "关闭" },
            { id: "on", label: "开启" },
          ],
        },
      }],
    },
    {
      id: "openrouter",
      label: "OpenRouter",
      credentialGroup: "openrouter",
      defaultChatModel: "openrouter/google/gemini-3.7-flash",
      defaultMemoryModel: "openrouter/openai/gpt-5.6-luna",
      models: [{
        id: "openrouter/google/gemini-3.7-flash",
        label: "Google · Gemini 3.7 Flash",
        routeId: "openrouter:gemini",
        transportProfile: "openai-compatible",
        processingProfile: "text-only-v1",
        capabilities: {
          inputModalities: ["text"],
          acceptedMediaTypes: [],
          maxAttachmentBytes: 0,
          maxTotalAttachmentBytes: 0,
          maxAttachmentsPerTurn: 0,
          attachments: false,
          toolCalling: true,
          parallelToolCalls: true,
          structuredOutput: false,
          multimodalToolResults: false,
          reasoningReplay: false,
        },
      }],
    },
  ],
};

describe("model config", () => {
  beforeEach(() => setModelCatalogForTests(catalog));
  afterEach(() => setModelCatalogForTests(null));

  it("uses the runtime catalog as the capability source", () => {
    expect(modelProviders().map((provider) => provider.id)).toEqual([
      "deepseek",
      "kimi",
      "openrouter",
    ]);
    expect(modelOption("kimi/vision")?.capabilities.inputModalities).toEqual([
      "text",
      "image",
      "document",
    ]);
    expect(modelOption("deepseek/deepseek-v4-flash-vision-exp")).toMatchObject({
      label: "DeepSeek V4 Flash Vision",
      processingProfile: "deepseek-vision-files-v1",
      capabilities: {
        inputModalities: ["text", "image"],
        acceptedMediaTypes: ["image/jpeg", "image/png", "image/webp"],
        attachments: true,
      },
    });
  });

  it("derives an atomic runtime config and display label from the selected model", () => {
    expect(defaultModelRuntimeConfig("kimi/vision")).toEqual({
      model: "kimi/vision",
      reasoningPreset: "on",
    });
    expect(modelRuntimeConfigLabel({ model: "kimi/vision", reasoningPreset: "off" })).toBe(
      "vision · 关闭",
    );
    expect(defaultModelRuntimeConfig("deepseek/deepseek-v4-flash-vision-exp")).toEqual({
      model: "deepseek/deepseek-v4-flash-vision-exp",
      reasoningPreset: "off",
    });
  });

  it("derives credential groups from catalog ownership", () => {
    expect(credentialGroupForModel("deepseek/chat")).toBe("deepseek");
    expect(credentialGroupForModel("kimi/vision")).toBe("kimi");
    expect(credentialGroupForModel("openrouter/google/gemini-3.7-flash")).toBe(
      "openrouter",
    );
    expect(credentialGroupForModel("custom/model")).toBeNull();
  });

  it("keeps empty catalog selections referentially stable for external-store consumers", () => {
    expect(modelProvidersFromCatalog(null)).toBe(modelProvidersFromCatalog(null));
    expect(providerModelsFromCatalog(null, "deepseek")).toBe(
      providerModelsFromCatalog(null, "deepseek"),
    );
  });

  it("derives provider models from the subscribed catalog snapshot", () => {
    expect(providerModelsFromCatalog(catalog, "kimi").map((model) => model.id)).toEqual([
      "kimi/vision",
    ]);
  });

  it("resolves the selected conversation model", () => {
    expect(resolveDefaultConversationModel({ chatModel: { provider: "kimi", model: "kimi/vision" } })).toBe("kimi/vision");
  });

  it("uses catalog defaults when a model is stale", () => {
    expect(defaultModelSelection("deepseek", "memory")).toEqual({
      provider: "deepseek",
      model: "deepseek/memory",
    });
    expect(normalizeModelSelection({ provider: "kimi", model: "stale" }, "chat")).toEqual({
      provider: "kimi",
      model: "kimi/vision",
    });
  });
});
