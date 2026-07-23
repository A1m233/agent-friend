import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  defaultModelSelection,
  modelOption,
  modelProviders,
  modelProvidersFromCatalog,
  normalizeModelSelection,
  providerModelsFromCatalog,
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
      ],
    },
    {
      id: "kimi",
      label: "Kimi",
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
      }],
    },
  ],
};

describe("model config", () => {
  beforeEach(() => setModelCatalogForTests(catalog));
  afterEach(() => setModelCatalogForTests(null));

  it("uses the runtime catalog as the capability source", () => {
    expect(modelProviders().map((provider) => provider.id)).toEqual(["deepseek", "kimi"]);
    expect(modelOption("kimi/vision")?.capabilities.inputModalities).toEqual([
      "text",
      "image",
      "document",
    ]);
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
