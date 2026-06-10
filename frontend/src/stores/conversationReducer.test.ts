import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import { applyAguiEvent } from "./conversationReducer";
import type { ChatMessage, TextBlock, ToolBlock } from "@/types/chat";

const base: ChatMessage = { id: "a", role: "assistant", blocks: [], status: "streaming" };

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

  it("RUN_ERROR → 消息标记 error 并带拟人文案", () => {
    const out = applyAguiEvent(
      base,
      emit({ type: EventType.RUN_ERROR, message: "我走神了一下" }),
    );
    expect(out.status).toBe("error");
    expect(out.error).toBe("我走神了一下");
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
