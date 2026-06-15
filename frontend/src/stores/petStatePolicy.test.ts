import { describe, expect, it } from "vitest";
import { defaultPetStatePolicy } from "./petStatePolicy";
import type { PushEnvelope, PushEvent } from "@/types/push";
import type { PetPhase } from "./petState";

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

const toolCallReq: PushEvent = {
  type: "tool_call_request",
  tool_call_id: "t1",
  tool_name: "search",
  args: {},
};
const toolCallRes: PushEvent = {
  type: "tool_call_result",
  tool_call_id: "t1",
  tool_name: "search",
  text: "ok",
  is_error: false,
  duration_seconds: 0.1,
};
const textDelta: PushEvent = { type: "text_delta", text: "你好" };
const done: PushEvent = { type: "done", stop_reason: "end_turn", total_tool_calls: 0 };

describe("defaultPetStatePolicy", () => {
  it("tool_call_request 触发进 thinking 态", () => {
    const t = defaultPetStatePolicy(envelope({ events: [toolCallReq] }), "idle");
    expect(t.next).toBe("thinking");
    expect(t.delayMs).toBeUndefined();
  });

  it("tool_call_result 也触发进 thinking 态", () => {
    const t = defaultPetStatePolicy(envelope({ events: [toolCallRes] }), "speaking");
    expect(t.next).toBe("thinking");
  });

  it("text_delta 优先级高于 tool_call（同帧 tool_call + text_delta 进 speaking）", () => {
    const t = defaultPetStatePolicy(
      envelope({ events: [toolCallReq, textDelta] }),
      "idle",
    );
    expect(t.next).toBe("speaking");
  });

  it("text_delta 触发进 speaking 态（从 thinking 切过来）", () => {
    const t = defaultPetStatePolicy(envelope({ events: [textDelta] }), "thinking");
    expect(t.next).toBe("speaking");
  });

  it("speaking 态收到 done → idle 延迟 300ms（lip-sync 收尾窗口）", () => {
    const t = defaultPetStatePolicy(envelope({ events: [done] }), "speaking");
    expect(t.next).toBe("idle");
    expect(t.delayMs).toBe(300);
    expect(t.from).toBe("speaking");
  });

  it("silent turn（thinking 直接 done 无 text_delta）→ idle 立即", () => {
    const t = defaultPetStatePolicy(envelope({ events: [done] }), "thinking");
    expect(t.next).toBe("idle");
    expect(t.delayMs).toBeUndefined();
  });

  it("user_turn 一律不切态", () => {
    const phases: PetPhase[] = ["idle", "thinking", "speaking", "error"];
    for (const p of phases) {
      const t = defaultPetStatePolicy(
        envelope({ kind: "user_turn", events: [textDelta] }),
        p,
      );
      expect(t.next).toBe(p);
    }
  });

  it("heartbeat 不切态", () => {
    const t = defaultPetStatePolicy(envelope({ kind: "heartbeat", events: [] }), "idle");
    expect(t.next).toBe("idle");
  });

  it("空 events 不切态", () => {
    const t = defaultPetStatePolicy(envelope({ events: [] }), "speaking");
    expect(t.next).toBe("speaking");
  });

  it("error 态收到下一个 tool_call_request 自动恢复进 thinking（错误恢复路径）", () => {
    const t = defaultPetStatePolicy(envelope({ events: [toolCallReq] }), "error");
    expect(t.next).toBe("thinking");
  });
});
