/**
 * 历史会话事件流 → 领域消息（{@link ChatMessage}）投影（纯函数，核心逻辑）。
 *
 * 切换到某历史会话时（GET /v1/sessions/{id} → events），把后端事件流投影成可渲染的
 * 消息列表，供继续对话（AC-M3.3：与 agent-cli 看到的是同一份会话视图）。
 *
 * 本期投影：user query 独立成消息；一轮 assistant answer 聚合成单条消息，blocks 内保持
 * text → tool → text 顺序（与实时 reducer 一致）。session_meta / persona_change 等元事件忽略；
 * 历史里的工具结果与实时同样兜底 [error] 前缀语义。
 *
 * 015 R-4.5.1 · 主动轮事件（014 引入）的两段处理：
 *   1. `system_trigger` event 本身 → 不投影（自然走 unknown type 兜底，但显式识别后用
 *      `output_visibility` 决定是否要"连带跳过紧接的 assistant_message"）。
 *   2. `output_visibility="user"` 的主动轮会落一条普通 `assistant_message`，**前端按
 *      R-4.4.2 不进 chat 窗 MessageList**——主动轮主出口是 pet 气泡。这里通过
 *      `pendingSkipAssistant` flag 让"紧跟 system_trigger 的 assistant_message"被跳过。
 *   3. `output_visibility="memory_only"`（silent turn）的主动轮不会落 assistant_message，
 *      flag 不需要置位（防御性兜底 false 即可）。
 *
 * 注意：本期沿用 014 v1 "单 session 假设"——主动轮一轮一条 assistant_message 紧跟
 * system_trigger，不会乱序穿插（详见 014 design §6 dispatch_system_turn 流程）。
 * 未来 chat 窗想回看历史主动轮：删掉 pendingSkipAssistant 即可，事件已在 JSONL 保留。
 */

import type {
  ChatAttachment,
  ChatMessage,
  ReminderBlock,
  TextBlock,
  ToolBlock,
} from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { TOOL_ERROR_PREFIX } from "./conversationReducer";

function textMessage(
  id: string,
  role: ChatMessage["role"],
  content: string,
  createdAt: string,
): ChatMessage {
  const block: TextBlock = { kind: "text", mid: id, text: content };
  return { id, role, createdAt, blocks: [block], status: "complete" };
}

function assistantMessage(id: string, createdAt: string): ChatMessage {
  return { id, role: "assistant", createdAt, blocks: [], status: "complete" };
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function attachmentsOf(value: unknown): ChatAttachment[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const data = item as Record<string, unknown>;
    const assetId = asString(data.asset_id);
    const displayName = asString(data.display_name);
    const mediaType = asString(data.media_type);
    const kind = data.kind === "image" || data.kind === "document" ? data.kind : null;
    if (!assetId || !displayName || !mediaType || !kind) return [];
    return [{
      assetId,
      displayName,
      mediaType,
      kind,
      sizeBytes: typeof data.size_bytes === "number" ? data.size_bytes : 0,
    }];
  });
}

export function projectSessionEvents(events: SessionEvent[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  // tool_call_id → 已落入某条消息的工具块引用，待 result 事件回填（同一对象，回填即生效）。
  const toolBlocks = new Map<string, ToolBlock>();
  const hiddenRunIds = new Set<string>();
  let currentAssistant: ChatMessage | null = null;
  // 015 R-4.5.1 · 见模块顶部注释：紧跟 user-visible system_trigger 的 assistant_message 是
  // 主动轮的"用户可见"输出，应被 chat 窗 MessageList 跳过（出口是 pet 气泡，不在 chat 窗）。
  let pendingSkipAssistant = false;
  // 只有紧邻持久化事实卡片的 Todo 主动轮，才允许把 Agent 增强回应投影进桌面对话。
  // 这既保护固定事实优先级，也兼容修复前已落盘的 IM orphan response。
  let pendingReminderCard = false;

  const ensureAssistant = (id: string, createdAt: string): ChatMessage => {
    if (currentAssistant) return currentAssistant;
    currentAssistant = assistantMessage(id, createdAt);
    messages.push(currentAssistant);
    return currentAssistant;
  };

  for (const ev of events) {
    const p = ev.payload ?? {};
    const runId = asString(ev.meta?.run_id);

    if (ev.type !== "system_trigger" && runId && hiddenRunIds.has(runId)) {
      continue;
    }

    if (ev.type === "system_trigger") {
      // user-visible 触发会紧跟一条 assistant_message——置 flag 让下一条跳过
      const visibility = asString(p.output_visibility);
      const presentation = asString(p.presentation);
      const sourceKind = asString(p.source_kind);
      const hasReminderCard = sourceKind === "todo_reminder" && pendingReminderCard;
      pendingReminderCard = false;
      if (presentation === "none" && runId) {
        hiddenRunIds.add(runId);
        currentAssistant = null;
        continue;
      }
      if (
        visibility === "user" &&
        (presentation !== "conversation" || (sourceKind === "todo_reminder" && !hasReminderCard))
      ) {
        pendingSkipAssistant = true;
      }
      continue;
    }

    if (ev.type === "todo_reminder") {
      // IM 已在唯一外部出口发送固定事实；session 仍保留审计/模型状态，但桌面不重复展示。
      if (asString(p.presentation) === "none") {
        pendingReminderCard = false;
        continue;
      }
      const block: ReminderBlock = {
        kind: "reminder",
        occurrenceId: asString(p.occurrence_id) || ev.uuid,
        todoId: asString(p.todo_id),
        reminderId: asString(p.reminder_id),
        title: asString(p.title),
        description: asString(p.description),
        scheduledAtUtc: asString(p.scheduled_at_utc),
        scheduledLocal: asString(p.scheduled_local),
        timezone: asString(p.timezone),
        recurrence: asString(p.recurrence),
        todoStatus: asString(p.todo_status),
      };
      // 主动提醒是独立消息，不得并入此前仍打开的 assistant turn。
      currentAssistant = null;
      ensureAssistant(`reminder:${block.occurrenceId}`, ev.ts).blocks.push(block);
      pendingReminderCard = true;
      continue;
    }

    if (ev.type === "user_message") {
      const message = textMessage(ev.uuid, "user", asString(p.content), ev.ts);
      message.attachments = attachmentsOf(p.attachments);
      messages.push(message);
      currentAssistant = null;
      pendingSkipAssistant = false;
      pendingReminderCard = false;
      continue;
    }

    if (ev.type === "assistant_message") {
      if (pendingSkipAssistant) {
        pendingSkipAssistant = false;  // 消费 flag、丢弃这条
        currentAssistant = null;
        continue;
      }
      const content = asString(p.content);
      const message = ensureAssistant(ev.uuid, ev.ts);
      if (content) {
        message.blocks.push({ kind: "text", mid: ev.uuid, text: content });
      }
      if (p.partial === true) {
        message.status = "interrupted";
      }
      continue;
    }

    if (ev.type === "run_error") {
      if (pendingSkipAssistant) {
        pendingSkipAssistant = false;
        currentAssistant = null;
        continue;
      }
      const message = ensureAssistant(ev.uuid, ev.ts);
      message.status = "error";
      message.error = asString(p.message) || "这轮回答中断了，没有生成完整回复。请稍后重试。";
      const code = asString(p.code);
      if (code) message.errorCode = code;
      currentAssistant = null;
      continue;
    }

    if (ev.type === "tool_call_request") {
      const eventToolCallId = asString(p.event_tool_call_id);
      const correlationId = eventToolCallId || asString(p.tool_call_id) || ev.uuid;
      const block: ToolBlock = {
        kind: "tool",
        // Legacy events may reuse a provider tool_call_id within one assistant turn.
        // The event UUID remains unique and stable for rendering; correlationId still
        // lets the matching result update the block below.
        toolCallId: eventToolCallId || ev.uuid,
        name: asString(p.tool_name) || "tool",
        // 历史里 args 是对象；序列化成字符串与实时 ToolBlock.args 类型对齐（ToolCard 再美化）。
        args: p.args === undefined ? "" : JSON.stringify(p.args),
        status: "running",
      };
      toolBlocks.set(correlationId, block);
      ensureAssistant(ev.uuid, ev.ts).blocks.push(block);
      continue;
    }

    if (ev.type === "tool_call_result") {
      const id = asString(p.event_tool_call_id) || asString(p.tool_call_id);
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
