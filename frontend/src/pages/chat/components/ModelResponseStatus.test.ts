import { describe, expect, it } from "vitest";
import type { ModelResponseProgress } from "@/types/chat";
import { modelResponseStatusLabel } from "./ModelResponseStatus";

function status(
  phase: ModelResponseProgress["phase"],
  afterTool = false,
): ModelResponseProgress {
  return {
    phase,
    afterTool,
    attempt: 1,
    maxAttempts: 2,
    activityVersion: 1,
  };
}

describe("modelResponseStatusLabel", () => {
  it("describes the request preparation placeholder immediately and when slow", () => {
    expect(modelResponseStatusLabel(undefined)).toBe("正在准备回答…");
    expect(modelResponseStatusLabel(undefined, { slow: true })).toBe(
      "准备回答所需内容花了较长时间，仍在继续…",
    );
  });

  it("uses a canonical run-level override for stopping state", () => {
    expect(modelResponseStatusLabel(status("processing"), {
      staleActivity: true,
      override: "正在取消当前任务，任务尚未结束…",
    })).toBe("正在取消当前任务，任务尚未结束…");
  });

  it("preserves waiting and post-tool progress labels", () => {
    expect(modelResponseStatusLabel(status("waiting"))).toBe("正在等待模型响应…");
    expect(modelResponseStatusLabel(status("waiting", true))).toBe("正在整理工具结果…");
  });

  it("keeps stale activity distinct from a healthy model stream", () => {
    expect(modelResponseStatusLabel(status("reasoning"))).toBe("模型仍在推理，连接正常…");
    expect(modelResponseStatusLabel(status("reasoning"), { staleActivity: true })).toBe(
      "暂时没有收到新的推理活动，模型可能仍在处理，也可能已经停滞…",
    );
  });
});
