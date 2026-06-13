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
    // memory_only 的 system_trigger 不带紧跟 assistant_message，所以投影结果应与不含时完全一致
    const withSilentSystemTrigger: SessionEvent[] = [
      ev("system_trigger", { source_kind: "idle_reflection", output_visibility: "memory_only" }),
      ...baseEvents,
      ev("memory_observation", { extracted: "用户在赶 deadline" }),
    ];
    expect(projectSessionEvents(withSilentSystemTrigger)).toEqual(projectSessionEvents(baseEvents));
  });

  it("user-visible system_trigger 紧跟的 assistant_message 也被跳过（015 R-4.5.1 / R-4.4.2）", () => {
    // 真实场景：BedtimeSource fire 后 session.events 里是 [user, asst, system_trigger(user), asst]
    // sessionProjection 应跳过 system_trigger 本身 + 紧跟的 asst（主动轮 user 可见输出归 pet 气泡）
    const events: SessionEvent[] = [
      ev("user_message", { content: "在吗" }),
      ev("assistant_message", { content: "在呢", partial: false }),
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("assistant_message", { content: "很晚了该睡了", partial: false }),
    ];
    const out = projectSessionEvents(events);
    expect(out).toHaveLength(2);  // 只剩 user "在吗" + asst "在呢"
    expect(out[0].role).toBe("user");
    expect(out[1].role).toBe("assistant");
    expect(out[1].blocks).toHaveLength(1);
    if (out[1].blocks[0].kind === "text") {
      expect(out[1].blocks[0].text).toBe("在呢");  // 主动轮的 "很晚了该睡了" 不应混进来
    }
  });

  it("user-visible system_trigger 之后再来的 user_message 不会被误跳过（flag 只消费一次）", () => {
    const events: SessionEvent[] = [
      ev("system_trigger", { source_kind: "cron:bedtime", output_visibility: "user" }),
      ev("assistant_message", { content: "很晚了", partial: false }),  // 被跳过
      ev("user_message", { content: "好我去睡" }),                       // 应保留
      ev("assistant_message", { content: "晚安", partial: false }),     // 应保留
    ];
    const out = projectSessionEvents(events);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe("user");
    expect((out[0].blocks[0] as { text: string }).text).toBe("好我去睡");
    expect(out[1].role).toBe("assistant");
    expect((out[1].blocks[0] as { text: string }).text).toBe("晚安");
  });
});
