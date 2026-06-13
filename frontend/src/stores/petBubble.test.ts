import { beforeEach, describe, expect, it } from "vitest";
import {
  resetPolicy,
  setPolicy,
  usePetBubbleStore,
} from "./petBubble";
import type { PushEnvelope } from "@/types/push";

function agentEnv(seq: number, content = "晚安"): PushEnvelope {
  return {
    kind: "agent_turn",
    session_id: "s1",
    seq,
    source_kind: "cron:bedtime",
    events: [
      { type: "text_delta", text: content },
      { type: "done", stop_reason: "end_turn", total_tool_calls: 0 },
    ],
  };
}

function userEnv(seq: number): PushEnvelope {
  return {
    kind: "user_turn",
    session_id: "s1",
    seq,
    source_kind: null,
    events: [
      { type: "text_delta", text: "user-triggered" },
      { type: "done", stop_reason: "end_turn", total_tool_calls: 0 },
    ],
  };
}

beforeEach(() => {
  usePetBubbleStore.setState({ phase: "idle", current: null });
  resetPolicy();
});

describe("usePetBubbleStore", () => {
  it("ingest 命中 policy → phase=showing + current 设上", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    const s = usePetBubbleStore.getState();
    expect(s.phase).toBe("showing");
    expect(s.current).toEqual({
      id: "s1:1",
      text: "晚安",
      sourceKind: "cron:bedtime",
    });
  });

  it("气泡常驻：ingest 后无自动消失（M15.8 决策；只能 dismiss 或被替换）", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    expect(usePetBubbleStore.getState().phase).toBe("showing");
    // 这里不引 fakeTimers——store 本身就不应该有 setTimeout 自动 idle
    // 反复读 state 应一直是 showing
    expect(usePetBubbleStore.getState().phase).toBe("showing");
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:1");
  });

  it("setPolicy 替换后能让 user_turn 也进气泡（AC-3 可替换扩展点）", () => {
    // 默认 policy：user_turn → null，气泡不冒
    usePetBubbleStore.getState().ingest(userEnv(1));
    expect(usePetBubbleStore.getState().phase).toBe("idle");

    // 替换 policy：把任意 envelope 都映射成 BubbleItem
    setPolicy((env) => ({
      id: `${env.session_id}:${env.seq}`,
      text: "all in",
      sourceKind: env.source_kind,
    }));
    usePetBubbleStore.getState().ingest(userEnv(2));
    expect(usePetBubbleStore.getState().phase).toBe("showing");
    expect(usePetBubbleStore.getState().current?.text).toBe("all in");
  });

  it("新 envelope（seq+1）替换 current", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "第一条"));
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:1");

    usePetBubbleStore.getState().ingest(agentEnv(2, "第二条"));
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:2");
    expect(usePetBubbleStore.getState().current?.text).toBe("第二条");
    expect(usePetBubbleStore.getState().phase).toBe("showing");
  });

  it("ingest 同 id（envelope 重发）不更新 current（防御性兜底）", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "原文"));
    const original = usePetBubbleStore.getState().current;

    // 同 session_id+seq 再 ingest 一次（极端兜底）
    usePetBubbleStore.getState().ingest(agentEnv(1, "篡改"));
    // current 应保持原对象（按 id 命中早 return）
    expect(usePetBubbleStore.getState().current).toBe(original);
  });

  it("expand 切到 expanded（无 timer 副作用、气泡仍常驻）", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    usePetBubbleStore.getState().expand();
    expect(usePetBubbleStore.getState().phase).toBe("expanded");
    expect(usePetBubbleStore.getState().current).not.toBeNull();
  });

  it("expand 仅对 showing 生效，idle / expanded 状态下是 no-op", () => {
    // idle → expand 不变
    usePetBubbleStore.getState().expand();
    expect(usePetBubbleStore.getState().phase).toBe("idle");

    // 进 expanded 后再 expand 一次：仍 expanded（不会"再 expand"破坏状态）
    usePetBubbleStore.getState().ingest(agentEnv(1));
    usePetBubbleStore.getState().expand();
    usePetBubbleStore.getState().expand();
    expect(usePetBubbleStore.getState().phase).toBe("expanded");
  });

  it("dismiss 强制切回 idle 并清 current", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    usePetBubbleStore.getState().dismiss();
    const s = usePetBubbleStore.getState();
    expect(s.phase).toBe("idle");
    expect(s.current).toBeNull();
  });

  it("从 expanded 也能 dismiss", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    usePetBubbleStore.getState().expand();
    usePetBubbleStore.getState().dismiss();
    expect(usePetBubbleStore.getState().phase).toBe("idle");
    expect(usePetBubbleStore.getState().current).toBeNull();
  });
});
