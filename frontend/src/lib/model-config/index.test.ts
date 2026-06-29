import { describe, expect, it } from "vitest";
import {
  DEEPSEEK_MODELS,
  isDeepseekModel,
  resolveDefaultConversationModel,
} from "./index";

describe("model config", () => {
  it("only allows the two desktop DeepSeek models", () => {
    expect(DEEPSEEK_MODELS).toEqual([
      "deepseek/deepseek-v4-flash",
      "deepseek/deepseek-v4-pro",
    ]);
    expect(isDeepseekModel("deepseek/deepseek-v4-flash")).toBe(true);
    expect(isDeepseekModel("deepseek/deepseek-v4-pro")).toBe(true);
    expect(isDeepseekModel("openai/gpt-4.1")).toBe(false);
  });

  it("resolves the selected model with a stable fallback", () => {
    expect(
      resolveDefaultConversationModel({
        deepseekModel: "deepseek/deepseek-v4-flash",
      }),
    ).toBe("deepseek/deepseek-v4-flash");

    expect(
      resolveDefaultConversationModel({
        deepseekModel: "not-a-model" as never,
      }),
    ).toBe("deepseek/deepseek-v4-pro");
  });
});
