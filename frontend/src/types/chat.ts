/**
 * 对话域领域模型（前端自有，**不**直接复用 @ag-ui/core 或 tdesign-chat 的类型）。
 *
 * 这层是隔离边界：上游 AG-UI 事件经 stores/conversationReducer 投影成这里的
 * {@link ChatMessage}；下游渲染时再由 pages/chat 投影成 tdesign-chat 的内容块。
 * 将来换协议或换 UI 库都只动投影层，领域模型不动（见 010 design §4.5 / R-M2.5）。
 */

export type ChatRole = "user" | "assistant";

/** 单条消息整体状态。 */
export type MessageStatus = "streaming" | "complete" | "interrupted" | "error";

/** 工具调用卡片状态机：进行中 → 完成 / 失败（对应 R-M3.2）。 */
export type ToolStatus = "running" | "done" | "error";

export interface ModelResponseProgress {
  phase: "waiting" | "reasoning" | "processing" | "retrying";
  afterTool: boolean;
  attempt: number;
  maxAttempts: number;
  activityVersion: number;
}

/** 文本块：assistant 一段连续文本（markdown）。AG-UI 把被工具调用打断的两段文本
 * 视为不同 message_id，这里对应 {@link ChatMessage.blocks} 里的两个文本块。 */
export interface TextBlock {
  kind: "text";
  /** AG-UI message_id；用于把后续 delta 归位到正确的文本块。 */
  mid: string;
  text: string;
}

/** 工具调用块（卡片）。 */
export interface ToolBlock {
  kind: "tool";
  toolCallId: string;
  name: string;
  /** 整段 args JSON 字符串（bridge 一次性下发，见 006 encoders）。 */
  args: string;
  /** 工具结果文本；运行中为 undefined。 */
  result?: string;
  status: ToolStatus;
}

/**
 * 思考块（reasoning）。**本期挂起渲染**：bridge 暂不发 reasoning 事件
 * （见 docs/issues/002），这里仅作结构预留，后端补事件后渲染层即可接上。
 */
export interface ThinkingBlock {
  kind: "thinking";
  text: string;
}

/** 到期提醒的持久化事实块；字段来自 todo_reminder session event，不经过模型。 */
export interface ReminderBlock {
  kind: "reminder";
  occurrenceId: string;
  todoId: string;
  reminderId: string;
  title: string;
  description: string;
  scheduledAtUtc: string;
  scheduledLocal: string;
  timezone: string;
  recurrence: string;
  todoStatus: string;
}

/** 上下文压缩活动；完成事实来自持久化 compaction session event。 */
export interface CompactionBlock {
  kind: "compaction";
  activityId: string;
  status: "running" | "complete";
}

export type MessageBlock =
  | TextBlock
  | ToolBlock
  | ThinkingBlock
  | ReminderBlock
  | CompactionBlock;

export interface ChatAttachment {
  assetId: string;
  displayName: string;
  kind: "image" | "document";
  mediaType: string;
  sizeBytes: number;
}

export interface ChatTextReference {
  referenceId: string;
  quotedText: string | null;
  sourceRole: "user" | "assistant" | "unknown";
  availability: "available" | "unavailable";
  source: {
    kind: "session_message" | "external_message";
    messageEventUuid: string | null;
    textBlockId: string | null;
    externalSourceKey: string | null;
  };
  locator: {
    kind: "text_range" | "whole_message";
    coordinateSpace: "rendered_text_utf16_v1" | null;
    start: number | null;
    end: number | null;
  };
}

/** 一条对话消息（user 或一整轮 assistant，assistant 轮可含多块文本/工具）。 */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  /** 消息创建时间，ISO 字符串；历史消息来自后端事件 ts，实时消息由前端生成。 */
  createdAt: string;
  blocks: MessageBlock[];
  attachments?: ChatAttachment[];
  references?: ChatTextReference[];
  status: MessageStatus;
  /** status==='error' 时的用户可见错误文案（不含技术细节）。 */
  error?: string;
  /** 后端错误码，用于后续 retry / 诊断入口做类型化处理。 */
  errorCode?: string;
  /** 当前 run 的瞬态模型请求状态；不参与历史持久化。 */
  runStatus?: ModelResponseProgress;
}
