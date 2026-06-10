/**
 * 对话域领域模型（前端自有，**不**直接复用 @ag-ui/core 或 tdesign-chat 的类型）。
 *
 * 这层是隔离边界：上游 AG-UI 事件经 stores/conversationReducer 投影成这里的
 * {@link ChatMessage}；下游渲染时再由 pages/chat 投影成 tdesign-chat 的内容块。
 * 将来换协议或换 UI 库都只动投影层，领域模型不动（见 010 design §4.5 / R-M2.5）。
 */

export type ChatRole = "user" | "assistant";

/** 单条消息整体状态。 */
export type MessageStatus = "streaming" | "complete" | "error";

/** 工具调用卡片状态机：进行中 → 完成 / 失败（对应 R-M3.2）。 */
export type ToolStatus = "running" | "done" | "error";

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

export type MessageBlock = TextBlock | ToolBlock | ThinkingBlock;

/** 一条对话消息（user 或一整轮 assistant，assistant 轮可含多块文本/工具）。 */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  blocks: MessageBlock[];
  status: MessageStatus;
  /** status==='error' 时的拟人化兜底文案（不含技术细节，见 R-M3.6）。 */
  error?: string;
}
