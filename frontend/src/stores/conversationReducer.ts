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
  type RunFinishedEvent,
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
import type { PushEvent } from "@/types/push";

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
      if (e.name === "agent_friend.run_status") {
        const runStatus = parseRunStatus(e.value);
        return runStatus ? {
          ...msg,
          status: "streaming",
          runStatus: {
            ...runStatus,
            activityVersion: (msg.runStatus?.activityVersion ?? 0) + 1,
          },
        } : msg;
      }
      if (e.name === "agent_friend.skill_activation") {
        const block = skillActivationBlock(e.value, "caller-skill");
        return block ? appendUniqueTool(msg, block) : msg;
      }
      return msg;
    }

    case EventType.RUN_FINISHED: {
      const e = evt as RunFinishedEvent;
      return {
        ...msg,
        status: isCancelledRunResult(e.result) ? "interrupted" : "complete",
        runStatus: undefined,
      };
    }

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

function isCancelledRunResult(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  return (value as Record<string, unknown>).outcome === "cancelled";
}

/**
 * 把 Runtime 主动续跑的原始 ConversationEvent 应用到 assistant 消息。
 *
 * 普通用户轮继续走 AG-UI；这里只在 Tauri push 的 ``run_event`` 通道使用。
 * 映射语义与 bridge 的 AG-UI encoder 保持一致，但文本块 id 由 run + 块序号
 * 稳定生成，因为原始 TextDelta 本身没有 message id。
 */
export function applyPushConversationEvent(
  msg: ChatMessage,
  evt: PushEvent,
  runId: string,
): ChatMessage {
  switch (evt.type) {
    case "text_delta": {
      const text = typeof evt.text === "string" ? evt.text : "";
      if (!text) return msg;
      const last = msg.blocks.at(-1);
      if (last?.kind === "text") {
        const blocks = [...msg.blocks];
        blocks[blocks.length - 1] = { ...last, text: last.text + text };
        return { ...msg, status: "streaming", runStatus: undefined, blocks };
      }
      const textIndex = msg.blocks.filter((block) => block.kind === "text").length;
      return {
        ...msg,
        status: "streaming",
        runStatus: undefined,
        blocks: [
          ...msg.blocks,
          { kind: "text", mid: `${runId}:text:${textIndex}`, text } satisfies TextBlock,
        ],
      };
    }

    case "tool_call_request": {
      const toolCallId = pushString(evt.event_tool_call_id) || pushString(evt.tool_call_id);
      if (!toolCallId) return msg;
      if (msg.blocks.some((block) => block.kind === "tool" && block.toolCallId === toolCallId)) {
        return msg;
      }
      const block: ToolBlock = {
        kind: "tool",
        toolCallId,
        name: pushString(evt.tool_name) || "tool",
        args: evt.args === undefined ? "" : JSON.stringify(evt.args),
        status: "running",
      };
      return { ...msg, status: "streaming", runStatus: undefined, blocks: [...msg.blocks, block] };
    }

    case "tool_call_result": {
      const toolCallId = pushString(evt.event_tool_call_id) || pushString(evt.tool_call_id);
      if (!toolCallId) return msg;
      return {
        ...msg,
        blocks: patchTool(msg.blocks, toolCallId, (tool) => ({
          ...tool,
          result: pushString(evt.text),
          status: evt.is_error === true ? "error" : "done",
        })),
      };
    }

    case "model_response_status": {
      const runStatus = parseRunStatus({
        phase: evt.phase,
        afterTool: evt.after_tool,
        attempt: evt.attempt,
        maxAttempts: evt.max_attempts,
      });
      return runStatus ? {
        ...msg,
        status: "streaming",
        runStatus: {
          ...runStatus,
          activityVersion: (msg.runStatus?.activityVersion ?? 0) + 1,
        },
      } : msg;
    }

    case "skill_activation_status": {
      const block = skillActivationBlock({
        status: evt.status,
        skillId: evt.skill_id,
        name: evt.name,
        source: evt.source,
        invocationKind: evt.invocation_kind,
        reason: evt.reason,
        errorCode: evt.error_code,
        errorMessage: evt.error_message,
        order: evt.order,
      }, `${runId}:caller-skill`);
      return block ? appendUniqueTool(msg, block) : msg;
    }

    case "done":
      return { ...msg, status: "complete", runStatus: undefined };

    default:
      return msg;
  }
}

function pushString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseRunStatus(value: unknown): ModelResponseProgress | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const phase = candidate.phase;
  const afterTool = candidate.afterTool;
  const attempt = candidate.attempt;
  const maxAttempts = candidate.maxAttempts;
  if (
    (
      phase !== "waiting"
      && phase !== "reasoning"
      && phase !== "processing"
      && phase !== "retrying"
    )
    || typeof afterTool !== "boolean"
    || typeof attempt !== "number"
    || typeof maxAttempts !== "number"
  ) return null;
  return { phase, afterTool, attempt, maxAttempts, activityVersion: 0 };
}

function skillActivationBlock(value: unknown, idPrefix: string): ToolBlock | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const status = candidate.status;
  const skillId = pushString(candidate.skillId);
  if ((status !== "activated" && status !== "error") || !skillId) return null;
  const order = typeof candidate.order === "number" ? candidate.order : 0;
  const args = {
    id: skillId,
    name: pushString(candidate.name),
    source: pushString(candidate.source),
    invocation_kind: pushString(candidate.invocationKind) || "caller",
    reason: pushString(candidate.reason),
  };
  const errorMessage = pushString(candidate.errorMessage);
  return {
    kind: "tool",
    toolCallId: `${idPrefix}:${skillId}:${order}`,
    name: "skill_read",
    args: JSON.stringify(args),
    result: status === "activated" ? "Skill 已加载" : errorMessage || "Skill 加载失败",
    status: status === "activated" ? "done" : "error",
  };
}

function appendUniqueTool(msg: ChatMessage, block: ToolBlock): ChatMessage {
  if (msg.blocks.some((item) => item.kind === "tool" && item.toolCallId === block.toolCallId)) {
    return msg;
  }
  return { ...msg, status: "streaming", blocks: [...msg.blocks, block] };
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
