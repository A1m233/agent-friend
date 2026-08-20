import { describe, expect, it } from "vitest";
import type { RuntimeCredentialGroupSnapshot } from "@/lib/runtime-config";
import { runtimeCredentialStatusLabel } from "./RuntimeCredentialGroupEditor";

function snapshot(
  configured: boolean,
  validation: RuntimeCredentialGroupSnapshot["validation"],
  partiallyConfigured = false,
): RuntimeCredentialGroupSnapshot {
  return {
    group: "deepseek",
    items: [],
    configured,
    partiallyConfigured,
    missingRequiredFields: configured ? [] : ["DEEPSEEK_API_KEY"],
    validation,
    effect: "restartBridge",
    operation: { phase: "idle", persisted: false, applied: false },
  };
}

describe("runtime credential status labels", () => {
  it("keeps an unvalidated credential neutral instead of showing 未验证", () => {
    const label = runtimeCredentialStatusLabel(snapshot(true, "none"));
    expect(label).toBe("已配置");
    expect(label).not.toContain("未验证");
  });

  it("shows only evidence-backed positive and negative states", () => {
    expect(runtimeCredentialStatusLabel(snapshot(true, "valid"))).toBe(
      "已配置 · 已验证",
    );
    expect(runtimeCredentialStatusLabel(snapshot(true, "invalid"))).toBe(
      "凭据有误",
    );
  });

  it("keeps partial multi-field configuration distinct from unconfigured", () => {
    expect(runtimeCredentialStatusLabel(snapshot(false, "none", true))).toBe(
      "配置不完整",
    );
    expect(runtimeCredentialStatusLabel(snapshot(false, "none"))).toBe("未配置");
  });
});
