import { describe, expect, it } from "vitest";
import { defaultPolicy } from "./petBubblePolicy";
import type { PushEnvelope, PushEvent } from "@/types/push";

function envelope(overrides: Partial<PushEnvelope>): PushEnvelope {
  return {
    kind: "agent_turn",
    session_id: "s1",
    seq: 1,
    source_kind: null,
    events: [],
    ...overrides,
  };
}

function delta(text: string): PushEvent {
  return { type: "text_delta", text };
}

function doneEv(): PushEvent {
  return { type: "done", stop_reason: "end_turn", total_tool_calls: 0 };
}

describe("defaultPolicy", () => {
  it("agent_turn 含 text_delta 流式增量时返 BubbleItem（AC-4）", () => {
    const env = envelope({
      seq: 7,
      source_kind: "cron:bedtime",
      events: [delta("很晚了，"), delta("该睡了。"), doneEv()],
    });
    const item = defaultPolicy(env);
    expect(item).toEqual({
      id: "s1:7",
      text: "很晚了，该睡了。",
      sourceKind: "cron:bedtime",
    });
  });

  it("silent turn（无 text_delta、仅 done）返 null（AC-5）", () => {
    const env = envelope({
      seq: 8,
      source_kind: "idle_reflection",
      events: [doneEv()],
    });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("user_turn 一律丢弃（兜底，正常情况下 Rust 订阅已过滤）", () => {
    const env = envelope({
      kind: "user_turn",
      events: [delta("用户触发轮回复")],
    });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("heartbeat 一律丢弃（兜底，正常情况下 Rust 已丢）", () => {
    const env = envelope({ kind: "heartbeat", session_id: "", seq: 0, events: [] });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("多段 text_delta 按 envelope 顺序拼接（不加分隔符，turn 内时序就是文本顺序）", () => {
    const env = envelope({
      events: [
        delta("第一"),
        delta("段。"),
        delta("第二"),
        delta("段。"),
        doneEv(),
      ],
    });
    expect(defaultPolicy(env)?.text).toBe("第一段。第二段。");
  });

  it("text_delta 的 text 不是 string 或为空时跳过", () => {
    const env = envelope({
      events: [
        { type: "text_delta", text: "" },
        { type: "text_delta", text: 42 },
        { type: "text_delta" },  // 无 text 字段
        doneEv(),
      ],
    });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("envelope 含 tool_call_* 事件但无 text_delta（纯工具轮）返 null", () => {
    // 边界：主动轮内部走工具但没文本输出（仅工具结果）—— 第一版不冒气泡
    const env = envelope({
      events: [
        { type: "tool_call_request", tool_call_id: "c", tool_name: "x", args: {} },
        { type: "tool_call_result", tool_call_id: "c", tool_name: "x", text: "ok", is_error: false, duration_seconds: 0 },
        doneEv(),
      ],
    });
    expect(defaultPolicy(env)).toBeNull();
  });
});

