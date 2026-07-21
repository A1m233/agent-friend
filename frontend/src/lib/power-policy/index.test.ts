import { describe, expect, it } from "vitest";
import {
  describePowerPolicy,
  reasonLabels,
  type PowerPolicySnapshot,
} from ".";

const base: PowerPolicySnapshot = {
  platform: "windows",
  policy: "pluggedIn",
  powerSource: "ac",
  reasons: [],
  capability: "supported",
  inhibitorState: "inactive",
  bridgeState: "available",
  lastError: null,
};

describe("power policy presentation", () => {
  it("shows all active reasons", () => {
    const presentation = describePowerPolicy({
      ...base,
      reasons: ["agentTask", "imOnline"],
      inhibitorState: "active",
    });

    expect(presentation.tone).toBe("success");
    expect(presentation.detail).toContain("Agent 正在执行任务");
    expect(presentation.detail).toContain("QQ 保持在线");
    expect(reasonLabels(["agentTask", "imOnline"])).toHaveLength(2);
  });

  it("does not claim success when plugged-in power is unknown", () => {
    const presentation = describePowerPolicy({
      ...base,
      reasons: ["imOnline"],
      powerSource: "unknown",
    });

    expect(presentation.tone).toBe("warning");
    expect(presentation.label).toContain("无法确认供电状态");
  });

  it("reports unsupported platforms explicitly", () => {
    const presentation = describePowerPolicy({
      ...base,
      platform: "linux",
      capability: "unsupported",
    });

    expect(presentation.label).toContain("暂不支持");
  });

  it("prioritizes follow-system over unavailable bridge state", () => {
    const presentation = describePowerPolicy({
      ...base,
      policy: "followSystem",
      bridgeState: "unavailable",
    });

    expect(presentation.label).toContain("跟随系统");
  });
});
