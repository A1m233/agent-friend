import { beforeEach, describe, expect, it } from "vitest";
import { resetPolicy, setPolicy, usePetBubbleStore } from "./petBubble";
import type { PushEnvelope } from "@/types/push";

function agentEnv(
  seq: number,
  content = "晚安",
  sourceKind: string | null = "cron:bedtime",
): PushEnvelope {
  return {
    kind: "agent_turn",
    session_id: "s1",
    seq,
    source_kind: sourceKind,
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
  usePetBubbleStore.setState({ items: [] });
  resetPolicy();
});

describe("usePetBubbleStore", () => {
  it("ingest 命中 policy 后新增一个常驻条目", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    expect(usePetBubbleStore.getState().items).toEqual([
      {
        id: "s1:1",
        text: "晚安",
        sourceKind: "cron:bedtime",
        expanded: false,
      },
    ]);
  });

  it("没有自动消失 timer，反复读取仍保留条目", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1));
    expect(usePetBubbleStore.getState().items).toHaveLength(1);
    expect(usePetBubbleStore.getState().items[0]?.id).toBe("s1:1");
  });

  it("setPolicy 替换后能让 user_turn 也进气泡", () => {
    usePetBubbleStore.getState().ingest(userEnv(1));
    expect(usePetBubbleStore.getState().items).toEqual([]);

    setPolicy((env) => ({
      id: `${env.session_id}:${env.seq}`,
      text: "all in",
      sourceKind: env.source_kind,
    }));
    usePetBubbleStore.getState().ingest(userEnv(2));
    expect(usePetBubbleStore.getState().items[0]?.text).toBe("all in");
  });

  it("普通主动轮继续替换上一条普通主动轮", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "第一条"));
    usePetBubbleStore.getState().ingest(agentEnv(2, "第二条"));
    expect(usePetBubbleStore.getState().items.map((item) => item.id)).toEqual(["s1:2"]);
    expect(usePetBubbleStore.getState().items[0]?.text).toBe("第二条");
  });

  it("同时到达的 Reminder 独立保留，不互相覆盖", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "提醒一", "todo_reminder"));
    usePetBubbleStore.getState().ingest(agentEnv(2, "提醒二", "todo_reminder"));
    expect(usePetBubbleStore.getState().items.map((item) => item.text)).toEqual([
      "提醒一",
      "提醒二",
    ]);
  });

  it("普通主动轮替换不会冲掉尚未关闭的 Reminder", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "提醒", "todo_reminder"));
    usePetBubbleStore.getState().ingest(agentEnv(2, "主动一"));
    usePetBubbleStore.getState().ingest(agentEnv(3, "主动二"));
    expect(usePetBubbleStore.getState().items.map((item) => item.text)).toEqual([
      "提醒",
      "主动二",
    ]);
  });

  it("ingest 同 id 的 envelope 重发时保持原条目", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "原文", "todo_reminder"));
    const original = usePetBubbleStore.getState().items[0];
    usePetBubbleStore.getState().ingest(agentEnv(1, "篡改", "todo_reminder"));
    expect(usePetBubbleStore.getState().items[0]).toBe(original);
  });

  it("expand 只展开指定条目", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "提醒一", "todo_reminder"));
    usePetBubbleStore.getState().ingest(agentEnv(2, "提醒二", "todo_reminder"));
    usePetBubbleStore.getState().expand("s1:2");
    expect(usePetBubbleStore.getState().items.map((item) => item.expanded)).toEqual([
      false,
      true,
    ]);
  });

  it("dismiss 只移除指定条目", () => {
    usePetBubbleStore.getState().ingest(agentEnv(1, "提醒一", "todo_reminder"));
    usePetBubbleStore.getState().ingest(agentEnv(2, "提醒二", "todo_reminder"));
    usePetBubbleStore.getState().dismiss("s1:1");
    expect(usePetBubbleStore.getState().items.map((item) => item.id)).toEqual(["s1:2"]);
  });
});
