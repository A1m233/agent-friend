/**
 * 历史会话事件流 → 领域消息（{@link ChatMessage}）投影（纯函数，核心逻辑）。
 *
 * 切换到某历史会话时（GET /v1/sessions/{id} → events），把后端事件流投影成可渲染的
 * 消息列表，供继续对话（AC-M3.3：与 agent-cli 看到的是同一份会话视图）。
 *
 * 本期投影：user / assistant 文本消息（保证上下文连续）+ 工具调用卡片（与实时一致，
 * 切走再切回不丢卡片）。session_meta / persona_change 等元事件忽略；历史里的工具结果
 * 与实时同样兜底 [error] 前缀语义。
 *
 * 015 R-4.5.1 · 014 引入的 `system_trigger` / `memory_observation` 主动轮事件
 * 不在本投影的已知分支里，会**自然跳过**（不投影到 chat 窗 MessageList）。这是
 * 015 期望的行为——主动轮 UI 出口是 pet 气泡而非 chat 窗。未来若要 chat 窗也回看
 * 历史主动轮，加一个 if 分支即可（事件已在 JSONL 保留，数据完整）。
 * 兜底测试见 sessionProjection.test.ts "忽略 014 引入的 system_trigger /
 * memory_observation 主动轮事件"。
 */

import type { ChatMessage, TextBlock, ToolBlock } from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { TOOL_ERROR_PREFIX } from "./conversationReducer";

function textMessage(id: string, role: ChatMessage["role"], content: string): ChatMessage {
  const block: TextBlock = { kind: "text", mid: id, text: content };
  return { id, role, blocks: [block], status: "complete" };
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function projectSessionEvents(events: SessionEvent[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  // tool_call_id → 已落入某条消息的工具块引用，待 result 事件回填（同一对象，回填即生效）。
  const toolBlocks = new Map<string, ToolBlock>();

  for (const ev of events) {
    const p = ev.payload ?? {};

    if (ev.type === "user_message") {
      messages.push(textMessage(ev.uuid, "user", asString(p.content)));
      continue;
    }

    if (ev.type === "assistant_message" && p.partial !== true) {
      const content = asString(p.content);
      if (content) messages.push(textMessage(ev.uuid, "assistant", content));
      continue;
    }

    if (ev.type === "tool_call_request") {
      const id = asString(p.tool_call_id) || ev.uuid;
      const block: ToolBlock = {
        kind: "tool",
        toolCallId: id,
        name: asString(p.tool_name) || "tool",
        // 历史里 args 是对象；序列化成字符串与实时 ToolBlock.args 类型对齐（ToolCard 再美化）。
        args: p.args === undefined ? "" : JSON.stringify(p.args),
        status: "running",
      };
      toolBlocks.set(id, block);
      messages.push({ id: ev.uuid, role: "assistant", blocks: [block], status: "complete" });
      continue;
    }

    if (ev.type === "tool_call_result") {
      const id = asString(p.tool_call_id);
      const block = toolBlocks.get(id);
      if (!block) continue;
      const content = asString(p.content);
      const isError = content.startsWith(TOOL_ERROR_PREFIX);
      block.result = isError ? content.slice(TOOL_ERROR_PREFIX.length) : content;
      block.status = isError ? "error" : "done";
      continue;
    }
  }

  return messages;
}
