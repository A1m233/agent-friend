import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUTO_DISMISS_MS,
  resetPolicy,
  setPolicy,
  usePetBubbleStore,
} from "./petBubble";
import type { PushEnvelope } from "@/types";

function agentEnv(seq: number, content = "晚安"): PushEnvelope {
  return {
    kind: "agent_turn",
    session_id: "s1",
    seq,
    source_kind: "cron:bedtime",
    events: [{ type: "assistant_message", uuid: "u", ts: "t", payload: { content } }],
  };
}

function userEnv(seq: number): PushEnvelope {
  return {
    kind: "user_turn",
    session_id: "s1",
    seq,
    source_kind: null,
    events: [{ type: "assistant_message", uuid: "u", ts: "t", payload: { content: "user-triggered" } }],
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  // 每条 case 前都 reset store + policy
  usePetBubbleStore.setState({ phase: "idle", current: null });
  resetPolicy();
});

afterEach(() => {
  vi.useRealTimers();
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

  it("ingest → showing → AUTO_DISMISS_MS 后自动 idle", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    expect(usePetBubbleStore.getState().phase).toBe("showing");
    vi.advanceTimersByTime(AUTO_DISMISS_MS);
    const s = usePetBubbleStore.getState();
    expect(s.phase).toBe("idle");
    expect(s.current).toBeNull();
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

  it("新 envelope（seq+1）替换 current 并重置 timer", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "第一条"));
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:1");

    // 5 秒后来第二条
    vi.advanceTimersByTime(5_000);
    usePetBubbleStore.getState().ingest(agentEnv(2, "第二条"));
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:2");
    expect(usePetBubbleStore.getState().current?.text).toBe("第二条");

    // 又过 5 秒：第二条还在（timer 已重置，距它入场只过了 5s，不到 10s）
    vi.advanceTimersByTime(5_000);
    expect(usePetBubbleStore.getState().phase).toBe("showing");
    expect(usePetBubbleStore.getState().current?.id).toBe("s1:2");

    // 再过 5 秒：第二条到点消失
    vi.advanceTimersByTime(5_000);
    expect(usePetBubbleStore.getState().phase).toBe("idle");
  });

  it("ingest 同 id（envelope 重发）不重置 timer / 不更新 current", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "原文"));
    const original = usePetBubbleStore.getState().current;
    vi.advanceTimersByTime(5_000);

    // 同 session_id+seq 再 ingest 一次（极端兜底）
    usePetBubbleStore.getState().ingest(agentEnv(1, "篡改"));
    // current 应保持原对象（按 id 命中早 return）
    expect(usePetBubbleStore.getState().current).toBe(original);

    // 再过 5s 触发自动 idle（timer 是第一次入场设的，没被重置）
    vi.advanceTimersByTime(5_000);
    expect(usePetBubbleStore.getState().phase).toBe("idle");
  });

  it("expand 切到 expanded 并清掉自动 dismiss timer", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    usePetBubbleStore.getState().expand();
    expect(usePetBubbleStore.getState().phase).toBe("expanded");

    // expanded 状态下过完整 AUTO_DISMISS_MS 也不自动消失
    vi.advanceTimersByTime(AUTO_DISMISS_MS * 2);
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

    // dismiss 后 timer 也清了：再过 AUTO_DISMISS_MS 不会出错（已 idle）
    vi.advanceTimersByTime(AUTO_DISMISS_MS);
    expect(usePetBubbleStore.getState().phase).toBe("idle");
  });
});
