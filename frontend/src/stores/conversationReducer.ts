/**
 * AG-UI 事件 → 领域消息（{@link ChatMessage}）的投影（纯函数，核心逻辑）。
 *
 * 把一轮 assistant 的 SSE 事件流累积进**单条 assistant 消息**的有序块列表：
 * 文本段（被工具调用打断会成多段）、工具调用卡片、（预留）思考块。
 *
 * 设计见 010 design §3.5 / §4.3；事件结构见 006 encoders。纯函数便于单测，
 * store 只负责把它套到"最后一条 assistant 消息"上。
 */

import {
  EventType,
  type BaseEvent,
  type CustomEvent,
  type RunErrorEvent,
  type TextMessageContentEvent,
  type TextMessageStartEvent,
  type ThinkingTextMessageContentEvent,
  type ToolCallArgsEvent,
  type ToolCallResultEvent,
  type ToolCallStartEvent,
} from "@ag-ui/core";
import type {
  ChatMessage,
  MessageBlock,
  ModelResponseProgress,
  TextBlock,
  ThinkingBlock,
  ToolBlock,
} from "@/types/chat";

/** 工具结果里 bridge 用此前缀表达 is_error（006 encoders：无专用字段，约定前缀）。 */
export const TOOL_ERROR_PREFIX = "[error] ";

/** 把一个 AG-UI 事件应用到 assistant 消息，返回新消息（不可变）。 */
export function applyAguiEvent(msg: ChatMessage, evt: BaseEvent): ChatMessage {
  switch (evt.type) {
    case EventType.TEXT_MESSAGE_START: {
      const e = evt as TextMessageStartEvent;
      const block: TextBlock = { kind: "text", mid: e.messageId, text: "" };
      return { ...msg, status: "streaming", runStatus: undefined, blocks: [...msg.blocks, block] };
    }

    case EventType.TEXT_MESSAGE_CONTENT: {
      const e = evt as TextMessageContentEvent;
      return {
        ...msg,
        status: "streaming",
        runStatus: undefined,
        blocks: appendText(msg.blocks, e.messageId, e.delta),
      };
    }

    case EventType.TOOL_CALL_START: {
      const e = evt as ToolCallStartEvent;
      const block: ToolBlock = {
        kind: "tool",
        toolCallId: e.toolCallId,
        name: e.toolCallName,
        args: "",
        status: "running",
      };
      return { ...msg, status: "streaming", runStatus: undefined, blocks: [...msg.blocks, block] };
    }

    case EventType.TOOL_CALL_ARGS: {
      const e = evt as ToolCallArgsEvent;
      return {
        ...msg,
        blocks: patchTool(msg.blocks, e.toolCallId, (t) => ({
          ...t,
          args: t.args + e.delta,
        })),
      };
    }

    case EventType.TOOL_CALL_RESULT: {
      const e = evt as ToolCallResultEvent;
      const isError = e.content.startsWith(TOOL_ERROR_PREFIX);
      const result = isError ? e.content.slice(TOOL_ERROR_PREFIX.length) : e.content;
      return {
        ...msg,
        blocks: patchTool(msg.blocks, e.toolCallId, (t) => ({
          ...t,
          result,
          status: isError ? "error" : "done",
        })),
      };
    }

    case EventType.THINKING_TEXT_MESSAGE_CONTENT: {
      // 预留：bridge 当前不发 reasoning 事件（issue 002）。结构先接上，便于后端补事件后即用。
      const e = evt as ThinkingTextMessageContentEvent;
      return { ...msg, status: "streaming", blocks: appendThinking(msg.blocks, e.delta) };
    }

    case EventType.CUSTOM: {
      const e = evt as CustomEvent;
      if (e.name !== "agent_friend.run_status") return msg;
      const runStatus = parseRunStatus(e.value);
      return runStatus ? { ...msg, status: "streaming", runStatus } : msg;
    }

    case EventType.RUN_FINISHED:
      return { ...msg, status: "complete", runStatus: undefined };

    case EventType.RUN_ERROR: {
      const e = evt as RunErrorEvent;
      // RUN_ERROR 是 turn 失败状态，不是 assistant 正常文本；文案由 bridge 按错误码清洗。
      return {
        ...msg,
        status: "error",
        error: e.message,
        errorCode: e.code,
        runStatus: undefined,
      };
    }

    default:
      // RUN_STARTED / TEXT_MESSAGE_END / TOOL_CALL_END / THINKING_* 边界事件等无需改状态。
      return msg;
  }
}

function parseRunStatus(value: unknown): ModelResponseProgress | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const phase = candidate.phase;
  const afterTool = candidate.afterTool;
  const attempt = candidate.attempt;
  const maxAttempts = candidate.maxAttempts;
  if (
    (phase !== "waiting" && phase !== "retrying")
    || typeof afterTool !== "boolean"
    || typeof attempt !== "number"
    || typeof maxAttempts !== "number"
  ) return null;
  return { phase, afterTool, attempt, maxAttempts };
}

function appendText(blocks: MessageBlock[], mid: string, delta: string): MessageBlock[] {
  const idx = blocks.findIndex((b) => b.kind === "text" && b.mid === mid);
  if (idx === -1) {
    // 没收到对应 START（容错）：补建一个文本块。
    return [...blocks, { kind: "text", mid, text: delta } satisfies TextBlock];
  }
  const next = [...blocks];
  const cur = next[idx] as TextBlock;
  next[idx] = { ...cur, text: cur.text + delta };
  return next;
}

function appendThinking(blocks: MessageBlock[], delta: string): MessageBlock[] {
  const last = blocks[blocks.length - 1];
  if (last && last.kind === "thinking") {
    const next = [...blocks];
    next[next.length - 1] = { ...last, text: last.text + delta };
    return next;
  }
  return [...blocks, { kind: "thinking", text: delta } satisfies ThinkingBlock];
}

function patchTool(
  blocks: MessageBlock[],
  toolCallId: string,
  fn: (t: ToolBlock) => ToolBlock,
): MessageBlock[] {
  const idx = blocks.findIndex((b) => b.kind === "tool" && b.toolCallId === toolCallId);
  if (idx === -1) return blocks;
  const next = [...blocks];
  next[idx] = fn(next[idx] as ToolBlock);
  return next;
}
