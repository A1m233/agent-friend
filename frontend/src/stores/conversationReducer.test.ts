import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import type { ChatMessage, TextBlock, ToolBlock } from "@/types/chat";

const base: ChatMessage = {
  id: "a",
  role: "assistant",
  createdAt: "2026-06-10T00:00:00Z",
  blocks: [],
  status: "streaming",
};

/** 构造一个 AG-UI 事件（测试里只关心 reducer 用到的字段）。 */
function emit(e: Record<string, unknown>): BaseEvent {
  return e as unknown as BaseEvent;
}

function reduce(msg: ChatMessage, events: Record<string, unknown>[]): ChatMessage {
  return events.reduce((m, e) => applyAguiEvent(m, emit(e)), msg);
}

describe("applyAguiEvent", () => {
  it("累加文本 delta 到同一文本块，RUN_FINISHED 标记完成", () => {
    const out = reduce(base, [
      { type: EventType.TEXT_MESSAGE_START, messageId: "m1", role: "assistant" },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m1", delta: "你好" },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m1", delta: "，世界" },
      { type: EventType.TEXT_MESSAGE_END, messageId: "m1" },
      { type: EventType.RUN_FINISHED, threadId: "t", runId: "r" },
    ]);
    expect(out.blocks).toHaveLength(1);
    expect((out.blocks[0] as TextBlock).text).toBe("你好，世界");
    expect(out.status).toBe("complete");
  });

  it("工具调用：START→ARGS→RESULT 推进卡片状态机到 done", () => {
    const out = reduce(base, [
      { type: EventType.TOOL_CALL_START, toolCallId: "t1", toolCallName: "search" },
      { type: EventType.TOOL_CALL_ARGS, toolCallId: "t1", delta: '{"q":"x"}' },
      { type: EventType.TOOL_CALL_END, toolCallId: "t1" },
      { type: EventType.TOOL_CALL_RESULT, messageId: "x", toolCallId: "t1", content: "结果" },
    ]);
    const tool = out.blocks[0] as ToolBlock;
    expect(tool.kind).toBe("tool");
    expect(tool.name).toBe("search");
    expect(tool.args).toBe('{"q":"x"}');
    expect(tool.result).toBe("结果");
    expect(tool.status).toBe("done");
  });

  it("工具结果 [error] 前缀 → 状态 error 且剥离前缀", () => {
    const out = reduce(base, [
      { type: EventType.TOOL_CALL_START, toolCallId: "t1", toolCallName: "search" },
      { type: EventType.TOOL_CALL_RESULT, messageId: "x", toolCallId: "t1", content: "[error] 炸了" },
    ]);
    const tool = out.blocks[0] as ToolBlock;
    expect(tool.status).toBe("error");
    expect(tool.result).toBe("炸了");
  });

  it("RUN_ERROR → 消息标记 error 并保留错误码", () => {
    const out = applyAguiEvent(
      base,
      emit({ type: EventType.RUN_ERROR, message: "模型服务这会儿过载了", code: "rate_limit" }),
    );
    expect(out.status).toBe("error");
    expect(out.error).toBe("模型服务这会儿过载了");
    expect(out.errorCode).toBe("rate_limit");
  });

  it("cancelled RUN_FINISHED → 标记为 interrupted，不冒充错误", () => {
    const out = applyAguiEvent(
      base,
      emit({
        type: EventType.RUN_FINISHED,
        threadId: "t",
        runId: "r",
        result: { outcome: "cancelled", reasonCode: "explicit_request" },
      }),
    );
    expect(out.status).toBe("interrupted");
    expect(out.error).toBeUndefined();
    expect(out.errorCode).toBeUndefined();
  });

  it("自定义 run status 投影重试状态，并在文本到达时清除", () => {
    const waiting = applyAguiEvent(
      base,
      emit({
        type: EventType.CUSTOM,
        name: "agent_friend.run_status",
        value: { phase: "retrying", afterTool: true, attempt: 2, maxAttempts: 2 },
      }),
    );
    expect(waiting.runStatus).toEqual({
      phase: "retrying",
      afterTool: true,
      attempt: 2,
      maxAttempts: 2,
    });

    const content = reduce(waiting, [
      { type: EventType.TEXT_MESSAGE_START, messageId: "m1", role: "assistant" },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m1", delta: "恢复了" },
    ]);
    expect(content.runStatus).toBeUndefined();
  });

  it("把调用方 Skill 激活状态投影成紧凑工具块", () => {
    const out = applyAguiEvent(
      base,
      emit({
        type: EventType.CUSTOM,
        name: "agent_friend.skill_activation",
        value: {
          status: "activated",
          skillId: "skill_2",
          name: "context7-mcp",
          source: "personal",
          invocationKind: "caller",
          reason: "由调用方为当前用户轮选择",
          order: 0,
        },
      }),
    );
    expect(out.blocks[0]).toMatchObject({
      kind: "tool",
      name: "skill_read",
      status: "done",
      result: "Skill 已加载",
    });
    expect(JSON.parse((out.blocks[0] as ToolBlock).args)).toMatchObject({
      id: "skill_2",
      name: "context7-mcp",
      invocation_kind: "caller",
    });
  });

  it("文本被工具调用打断 → 形成两个独立文本块", () => {
    const out = reduce(base, [
      { type: EventType.TEXT_MESSAGE_START, messageId: "m1", role: "assistant" },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m1", delta: "前段" },
      { type: EventType.TEXT_MESSAGE_END, messageId: "m1" },
      { type: EventType.TOOL_CALL_START, toolCallId: "t1", toolCallName: "search" },
      { type: EventType.TOOL_CALL_RESULT, messageId: "x", toolCallId: "t1", content: "ok" },
      { type: EventType.TEXT_MESSAGE_START, messageId: "m2", role: "assistant" },
      { type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m2", delta: "后段" },
    ]);
    expect(out.blocks.map((b) => b.kind)).toEqual(["text", "tool", "text"]);
    expect((out.blocks[0] as TextBlock).text).toBe("前段");
    expect((out.blocks[2] as TextBlock).text).toBe("后段");
  });
});

describe("applyPushConversationEvent", () => {
  it("projects proactive text/tool/text deltas incrementally", () => {
    const events = [
      { type: "text_delta", text: "先搜索" },
      {
        type: "tool_call_request",
        tool_call_id: "provider-id",
        event_tool_call_id: "event-id",
        tool_name: "social_search",
        args: { platform: "xiaohongshu", query: "AI Agent" },
      },
      {
        type: "tool_call_result",
        tool_call_id: "provider-id",
        event_tool_call_id: "event-id",
        tool_name: "social_search",
        text: "找到 10 条",
        is_error: false,
      },
      { type: "text_delta", text: "再总结" },
      { type: "done" },
    ];
    const out = events.reduce(
      (message, event) => applyPushConversationEvent(message, event, "run-1"),
      base,
    );

    expect(out.blocks.map((block) => block.kind)).toEqual(["text", "tool", "text"]);
    expect((out.blocks[0] as TextBlock).text).toBe("先搜索");
    expect(out.blocks[0]).toMatchObject({ mid: "run-1:text:0" });
    expect(out.blocks[1]).toMatchObject({
      toolCallId: "event-id",
      name: "social_search",
      status: "done",
      result: "找到 10 条",
    });
    expect((out.blocks[2] as TextBlock).text).toBe("再总结");
    expect(out.status).toBe("complete");
  });

  it("projects model response status and clears it on text", () => {
    const waiting = applyPushConversationEvent(
      base,
      {
        type: "model_response_status",
        phase: "retrying",
        after_tool: true,
        attempt: 2,
        max_attempts: 3,
      },
      "run-1",
    );
    expect(waiting.runStatus).toEqual({
      phase: "retrying",
      afterTool: true,
      attempt: 2,
      maxAttempts: 3,
    });
    expect(
      applyPushConversationEvent(waiting, { type: "text_delta", text: "恢复" }, "run-1")
        .runStatus,
    ).toBeUndefined();
  });

  it("projects pushed caller Skill failures truthfully", () => {
    const out = applyPushConversationEvent(
      base,
      {
        type: "skill_activation_status",
        status: "error",
        skill_id: "missing",
        invocation_kind: "caller",
        reason: "由调用方为当前用户轮选择",
        error_code: "skill_not_found",
        error_message: "Skill 不存在，未激活",
        order: 0,
      },
      "run-1",
    );
    expect(out.blocks[0]).toMatchObject({
      kind: "tool",
      name: "skill_read",
      status: "error",
      result: "Skill 不存在，未激活",
    });
  });
});
