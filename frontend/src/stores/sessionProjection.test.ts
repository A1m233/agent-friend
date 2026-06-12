import { describe, expect, it } from "vitest";
import { projectSessionEvents } from "./sessionProjection";
import type { SessionEvent } from "@/types/meta";

function ev(type: string, payload?: Record<string, unknown>): SessionEvent {
  return { type, uuid: `${type}-${Math.random()}`, ts: "2026-06-10T00:00:00Z", payload };
}

describe("projectSessionEvents", () => {
  it("投影 user / assistant 文本消息，忽略元事件", () => {
    const out = projectSessionEvents([
      ev("session_meta", { initial_title: "t" }),
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在的", partial: false }),
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    expect(out[0].status).toBe("complete");
  });

  it("跳过 partial assistant 消息与空内容", () => {
    const out = projectSessionEvents([
      ev("assistant_message", { content: "半截", partial: true }),
      ev("assistant_message", { content: "", partial: false }),
      ev("user_message", { content: "hi" }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].role).toBe("user");
  });

  it("把工具调用 request/result 重建成工具块（切回历史不丢卡片）", () => {
    const out = projectSessionEvents([
      ev("user_message", { content: "搜新闻" }),
      ev("assistant_message", { content: "我看看", partial: false }),
      ev("tool_call_request", {
        tool_call_id: "call_1",
        tool_name: "web_search",
        args: { query: "今日新闻" },
      }),
      ev("tool_call_result", {
        tool_call_id: "call_1",
        tool_name: "web_search",
        content: "搜索结果……",
      }),
      ev("assistant_message", { content: "结果如下", partial: false }),
    ]);
    expect(out).toHaveLength(4);
    const toolMsg = out[2];
    expect(toolMsg.role).toBe("assistant");
    expect(toolMsg.blocks).toHaveLength(1);
    const block = toolMsg.blocks[0];
    expect(block.kind).toBe("tool");
    if (block.kind === "tool") {
      expect(block.name).toBe("web_search");
      expect(block.status).toBe("done");
      expect(block.result).toBe("搜索结果……");
      expect(JSON.parse(block.args)).toEqual({ query: "今日新闻" });
    }
  });

  it("工具结果 [error] 前缀映射为 error 状态并剥前缀", () => {
    const out = projectSessionEvents([
      ev("tool_call_request", { tool_call_id: "c", tool_name: "t", args: {} }),
      ev("tool_call_result", { tool_call_id: "c", tool_name: "t", content: "[error] 超时了" }),
    ]);
    const block = out[0].blocks[0];
    expect(block.kind).toBe("tool");
    if (block.kind === "tool") {
      expect(block.status).toBe("error");
      expect(block.result).toBe("超时了");
    }
  });

  it("忽略 014 引入的 system_trigger / memory_observation 主动轮事件（015 R-4.5.1 / AC-6）", () => {
    const baseEvents: SessionEvent[] = [
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在的", partial: false }),
      ev("tool_call_request", { tool_call_id: "c", tool_name: "t", args: { q: "x" } }),
      ev("tool_call_result", { tool_call_id: "c", tool_name: "t", content: "ok" }),
      ev("assistant_message", { content: "好了", partial: false }),
    ];
    // 在 base events 中插入 014 主动轮事件（system_trigger 在头 + memory_observation 在尾）；
    // 投影出的 ChatMessage 序列应与不含时完全一致（断言 toEqual 兜底 AC-6）
    const withSystemTrigger: SessionEvent[] = [
      ev("system_trigger", { source_kind: "cron:bedtime" }),
      ...baseEvents,
      ev("memory_observation", { extracted: "用户在赶 deadline" }),
      ev("system_trigger", { source_kind: "idle_reflection" }),
    ];
    expect(projectSessionEvents(withSystemTrigger)).toEqual(projectSessionEvents(baseEvents));
  });
});
