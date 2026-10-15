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
  ChatTextReference,
  ToolBlock,
} from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { TOOL_ERROR_PREFIX } from "./conversationReducer";
import { normalizeAssistantPhase, parseAssistantPhaseState } from "./assistantPhase";
import { parseRunErrorRecovery } from "./taskRecovery";
import { parseAssistantContent } from "./assistantContent";
import { applyAssistantImage } from "./assistantImages";
import { setAssistantItem } from "./assistantPhase";
import { toolImagesOf } from "./toolImages";

function textMessage(
  id: string,
  role: ChatMessage["role"],
  content: string,
  createdAt: string,
): ChatMessage {
  const block: TextBlock = { kind: "text", mid: id, text: content };
  return { id, role, createdAt, blocks: [block], status: "complete" };
}

function assistantMessage(id: string, createdAt: string, runId?: string): ChatMessage {
  return { id, role: "assistant", runId, createdAt, blocks: [], status: "complete" };
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function interruptionMessage(reason: string): string {
  if (reason === "LLMStreamTimeoutError") {
    return "模型响应长时间没有进展，这轮回答已超时。你可以重试。";
  }
  return "这轮回答因运行错误中断了。你可以重试。";
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

function referencesOf(value: unknown): ChatTextReference[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const raw = item as Record<string, unknown>;
    const source = raw.source;
    const locator = raw.locator;
    if (!source || typeof source !== "object" || !locator || typeof locator !== "object") {
      return [];
    }
    const sourceRaw = source as Record<string, unknown>;
    const locatorRaw = locator as Record<string, unknown>;
    const availability = raw.availability;
    const sourceRole = raw.source_role;
    const sourceKind = sourceRaw.kind;
    const locatorKind = locatorRaw.kind;
    if (
      typeof raw.reference_id !== "string"
      || !["available", "unavailable"].includes(String(availability))
      || !["user", "assistant", "unknown"].includes(String(sourceRole))
      || !["session_message", "external_message"].includes(String(sourceKind))
      || !["text_range", "whole_message"].includes(String(locatorKind))
    ) return [];
    return [{
      referenceId: raw.reference_id,
      quotedText: typeof raw.quoted_text === "string" ? raw.quoted_text : null,
      sourceRole: sourceRole as ChatTextReference["sourceRole"],
      availability: availability as ChatTextReference["availability"],
      source: {
        kind: sourceKind as ChatTextReference["source"]["kind"],
        messageEventUuid: typeof sourceRaw.message_event_uuid === "string"
          ? sourceRaw.message_event_uuid
          : null,
        textBlockId: typeof sourceRaw.text_block_id === "string"
          ? sourceRaw.text_block_id
          : null,
        externalSourceKey: typeof sourceRaw.external_source_key === "string"
          ? sourceRaw.external_source_key
          : null,
      },
      locator: {
        kind: locatorKind as ChatTextReference["locator"]["kind"],
        coordinateSpace: locatorRaw.coordinate_space === "rendered_text_utf16_v1"
          ? "rendered_text_utf16_v1"
          : null,
        start: typeof locatorRaw.start === "number" ? locatorRaw.start : null,
        end: typeof locatorRaw.end === "number" ? locatorRaw.end : null,
      },
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

  const ensureAssistant = (id: string, createdAt: string, runId?: string): ChatMessage => {
    if (currentAssistant) {
      if (runId && currentAssistant.runId && currentAssistant.runId !== runId) {
        currentAssistant = null;
      } else {
        if (runId && !currentAssistant.runId) currentAssistant.runId = runId;
        return currentAssistant;
      }
    }
    currentAssistant = assistantMessage(id, createdAt, runId || undefined);
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
        if (runId) {
          hiddenRunIds.add(runId);
          pendingSkipAssistant = false;
          currentAssistant = null;
        }
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
      ensureAssistant(`reminder:${block.occurrenceId}`, ev.ts, runId).blocks.push(block);
      pendingReminderCard = true;
      continue;
    }

    if (ev.type === "compaction") {
      if (pendingSkipAssistant) continue;
      ensureAssistant(ev.uuid, ev.ts, runId).blocks.push({
        kind: "compaction",
        activityId: ev.uuid,
        status: "complete",
      });
      continue;
    }

    if (ev.type === "user_message") {
      const message = textMessage(ev.uuid, "user", asString(p.content), ev.ts);
      message.attachments = attachmentsOf(p.attachments);
      message.references = referencesOf(p.references);
      if (p.request_source && typeof p.request_source === "object") {
        const source = p.request_source as Record<string, unknown>;
        if (source.kind === "scheduled_task" && typeof source.task_id === "string" && typeof source.task_name === "string"
          && typeof source.occurrence_id === "string") {
          message.requestSource = { taskId: source.task_id, taskName: source.task_name, occurrenceId: source.occurrence_id };
        }
      }
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
      const message = ensureAssistant(ev.uuid, ev.ts, runId);
      // 一轮 assistant 可能包含多段文本和工具调用；左下角展示整轮最后回复的完成时间。
      message.createdAt = ev.ts;
      if (p.generated_image && typeof p.generated_image === "object") {
        const image = p.generated_image as Record<string, unknown>;
        message.blocks = applyAssistantImage(message, {
          type: "assistant_image", message_id: ev.uuid, item_index: p.item_index ?? 0,
          images: p.attachments, error_code: image.error_code,
        }).blocks;
      } else if (content) {
        message.blocks.push({ kind: "text", mid: ev.uuid, text: content,
          ...(p.content_parts ? { contentParts: parseAssistantContent(p.content_parts) } : {}),
          ...(p.partial === true ? { partial: true } : {}),
          ...(p.phase === undefined ? {} : { phase: normalizeAssistantPhase(p.phase) }) });
      }
      if (!p.generated_image && p.item_index !== undefined) {
        message.blocks = setAssistantItem(message.blocks, ev.uuid, p.phase, p.item_index);
      }
      if (p.partial === true) {
        const reason = asString(p.interruption_reason);
        if (reason && reason !== "explicit_request") {
          message.status = "error";
          message.errorCode = reason;
          message.error = interruptionMessage(reason);
        } else {
          message.status = "interrupted";
        }
      }
      continue;
    }

    if (ev.type === "skill_context") {
      const skillId = asString(p.skill_id) || asString(p.selection_id);
      const status = p.status === "activated" ? "done" : "error";
      const block: ToolBlock = {
        kind: "tool",
        toolCallId: `caller-skill:${ev.uuid}`,
        name: "skill_read",
        args: JSON.stringify({
          id: skillId,
          name: asString(p.name),
          source: asString(p.source),
          invocation_kind: "caller",
          reason: "由调用方为当前用户轮选择",
        }),
        result: status === "done"
          ? "Skill 已加载"
          : asString(p.error_message) || "Skill 加载失败",
        status,
      };
      ensureAssistant(ev.uuid, ev.ts, runId).blocks.push(block);
      continue;
    }

    if (ev.type === "run_error") {
      if (pendingSkipAssistant) {
        pendingSkipAssistant = false;
        currentAssistant = null;
        continue;
      }
      const message = ensureAssistant(ev.uuid, ev.ts, runId || asString(p.run_id) || `legacy:${ev.uuid}`);
      message.createdAt = ev.ts;
      message.status = "error";
      message.error = asString(p.message) || "这轮回答中断了，没有生成完整回复。请稍后重试。";
      const code = asString(p.code);
      if (code) message.errorCode = code;
      Object.assign(message, parseRunErrorRecovery(p, "snake"));
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
        // 095 起优先保留模型原始 arguments；旧事件继续从 args 对象重建。
        args: asString(p.arguments_json) || (p.args === undefined ? "" : JSON.stringify(p.args)),
        status: "running",
      };
      toolBlocks.set(correlationId, block);
      ensureAssistant(ev.uuid, ev.ts, runId).blocks.push(block);
      continue;
    }

    if (ev.type === "tool_call_result") {
      const id = asString(p.event_tool_call_id) || asString(p.tool_call_id);
      const block = toolBlocks.get(id);
      if (!block) continue;
      const content = asString(p.content);
      const hasLegacyErrorPrefix = content.startsWith(TOOL_ERROR_PREFIX);
      const isError = p.is_error === true || hasLegacyErrorPrefix;
      block.result = hasLegacyErrorPrefix ? content.slice(TOOL_ERROR_PREFIX.length) : content;
      block.status = isError ? "error" : "done";
      block.images = isError ? [] : toolImagesOf(p.images);
      continue;
    }
  }

  // A started user run may have crashed before producing any body. Keep a
  // header-only row after its user message; orphan/hidden metadata creates none.
  for (const ev of events) {
    const runId = asString(ev.meta?.run_id);
    if (ev.type !== "run_started" || !runId || hiddenRunIds.has(runId)
      || messages.some((message) => message.role === "assistant" && message.runId === runId)) continue;
    const userEvent = events.find((candidate) => candidate.type === "user_message" && candidate.meta?.run_id === runId);
    const userIndex = messages.findIndex((message) => message.id === userEvent?.uuid);
    const sourceRunId = asString(ev.payload?.source_run_id);
    const sourceIndex = sourceRunId
      ? messages.findIndex((message) => message.role === "assistant" && message.runId === sourceRunId)
      : -1;
    const anchorIndex = userIndex >= 0 ? userIndex : sourceIndex;
    if (anchorIndex < 0) continue;
    messages.splice(anchorIndex + 1, 0, { ...assistantMessage(`run:${runId}`, ev.ts, runId), status: "interrupted" });
  }
  // Apply durable facts to their own runs, never the currently selected model.
  for (const ev of events) {
    const runId = asString(ev.meta?.run_id);
    if (!runId) continue;
    for (const message of messages) {
      if (message.role !== "assistant" || message.runId !== runId) continue;
      if (ev.type === "assistant_phase") message.assistantPhase = parseAssistantPhaseState(ev.payload);
      if (ev.type === "run_started") {
        message.sourceRunId = asString(ev.payload?.source_run_id) || undefined;
        message.rootRunId = asString(ev.payload?.root_run_id) || undefined;
        message.runStartedAt = asString(ev.payload?.started_at) || undefined;
        if (message.status === "complete") message.status = "interrupted";
      }
      if (ev.type === "run_terminal") {
        message.runStartedAt = asString(ev.payload?.started_at) || message.runStartedAt;
        message.runFinishedAt = ev.payload?.recovered === true
          ? undefined : asString(ev.payload?.finished_at) || undefined;
        const status = ev.payload?.status;
        const outcome = status === "interrupted" ? "cancelled" : status;
        if (outcome === "completed") {
          message.runOutcome = "completed";
          message.status = "complete";
        } else if (outcome === "cancelled" || outcome === "failed" || outcome === "skipped"
          || outcome === "expired" || outcome === "rejected") {
          message.runOutcome = outcome;
          message.status = outcome === "failed" ? "error" : "interrupted";
        }
      }
    }
  }
  return messages;
}
