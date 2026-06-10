/**
 * 文本块 → tdesign-chat markdown 内容块的投影（渲染边界）。
 *
 * 这是隔离 alpha 版 tdesign-web-components 的下游边界：store 只认领域模型，
 * 这里临到渲染才把「一段文本」转成 `<ChatMessage content>` 吃的 markdown 块结构。
 *
 * 注意：工具调用块（{@link ToolBlock}）**不**走 tdesign —— 其 `<ChatMessage>`（alpha）
 * 不渲染 toolcall 块，故工具卡片由 {@link ToolCard} 自渲染（见 010 design §4.5）。
 * 思考块（thinking）本期挂起（issue 002）。
 */

/** tdesign-chat markdown 内容块（结构对齐 tdesign-web-components 的 AIMessageContent）。 */
export interface TdMarkdownBlock {
  type: "markdown";
  data: string;
}

/** 把一段文本投影成 tdesign 的 markdown 内容块数组（`<ChatMessage content>` 入参）。 */
export function toMarkdownContent(text: string): TdMarkdownBlock[] {
  return [{ type: "markdown", data: text }];
}
