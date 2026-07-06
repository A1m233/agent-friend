import { describe, expect, it } from "vitest";
import {
  MODEL_PROVIDERS,
  defaultModelSelection,
  normalizeModelSelection,
  resolveDefaultConversationModel,
} from "./index";

describe("model config", () => {
  it("exposes DeepSeek and Kimi provider models", () => {
    expect(MODEL_PROVIDERS.map((provider) => provider.id)).toEqual([
      "deepseek",
      "kimi",
    ]);
    expect(MODEL_PROVIDERS.find((provider) => provider.id === "kimi")?.models).toEqual(
      [
        { id: "kimi/kimi-k2.6", label: "kimi-k2.6" },
        { id: "kimi/kimi-k2.5", label: "kimi-k2.5" },
        { id: "kimi/kimi-k2.7-code", label: "kimi-k2.7-code" },
        {
          id: "kimi/kimi-k2.7-code-highspeed",
          label: "kimi-k2.7-code-highspeed",
        },
      ],
    );
  });

  it("resolves the selected conversation model", () => {
    expect(
      resolveDefaultConversationModel({
        chatModel: { provider: "kimi", model: "kimi/kimi-k2.6" },
      }),
    ).toBe("kimi/kimi-k2.6");
  });

  it("uses provider defaults when a model is stale", () => {
    expect(defaultModelSelection("deepseek", "memory")).toEqual({
      provider: "deepseek",
      model: "deepseek/deepseek-v4-pro",
    });
    expect(
      normalizeModelSelection(
        { provider: "kimi", model: "deepseek/deepseek-v4-flash" },
        "chat",
      ),
    ).toEqual({ provider: "kimi", model: "kimi/kimi-k2.6" });
  });
});
