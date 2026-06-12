import { describe, expect, it } from "vitest";
import { defaultPolicy } from "./petBubblePolicy";
import type { PushEnvelope, SessionEvent } from "@/types";

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

function ev(type: string, payload?: Record<string, unknown>): SessionEvent {
  return { type, uuid: "u", ts: "2026-06-13T00:00:00Z", payload };
}

describe("defaultPolicy", () => {
  it("agent_turn 含 assistant_message 时返 BubbleItem（AC-4）", () => {
    const env = envelope({
      seq: 7,
      source_kind: "cron:bedtime",
      events: [ev("assistant_message", { content: "很晚了，该睡了。" })],
    });
    const item = defaultPolicy(env);
    expect(item).toEqual({
      id: "s1:7",
      text: "很晚了，该睡了。",
      sourceKind: "cron:bedtime",
    });
  });

  it("silent turn（只有 memory_observation、无 assistant_message）返 null（AC-5）", () => {
    const env = envelope({
      seq: 8,
      source_kind: "idle_reflection",
      events: [
        ev("memory_observation", { extracted: "..." }),
        ev("system_trigger", { source_kind: "idle_reflection" }),
      ],
    });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("user_turn 一律丢弃（兜底，正常情况下 Rust 订阅已过滤）", () => {
    const env = envelope({
      kind: "user_turn",
      events: [ev("assistant_message", { content: "用户触发轮回复" })],
    });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("heartbeat 一律丢弃（兜底，正常情况下 Rust 已丢）", () => {
    const env = envelope({ kind: "heartbeat", session_id: "", seq: 0, events: [] });
    expect(defaultPolicy(env)).toBeNull();
  });

  it("多个 assistant_message 按 \\n\\n 拼接", () => {
    const env = envelope({
      events: [
        ev("assistant_message", { content: "第一段" }),
        ev("memory_observation", {}),
        ev("assistant_message", { content: "第二段" }),
      ],
    });
    const item = defaultPolicy(env);
    expect(item?.text).toBe("第一段\n\n第二段");
  });

  it("assistant_message 的 content 不是 string 或为空时跳过", () => {
    const env = envelope({
      events: [
        ev("assistant_message", { content: "" }),
        ev("assistant_message", { content: 42 }),
        ev("assistant_message"),  // 无 payload
      ],
    });
    expect(defaultPolicy(env)).toBeNull();
  });
});
