# 026 · tool-call carrier is modeled as empty assistant message

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-25
- **关联需求**：005-engine-tool-calling-and-web-search、020-engine-tool-conversation-history

## 现象

当前 session 事件流用一条 `assistant_message(content="")` 作为 tool-call 轮的协议承载：

1. LLM 决定调用工具，但本轮没有 assistant 文本。
2. `Conversation.stream()` 先落一条空 `assistant_message`。
3. 随后的 `tool_call_request` 事件在 `Session.messages` 投影时挂到前一条 assistant message 的 `meta["tool_calls"]` 上。
4. 再通过 `tool_call_result` 回喂 LLM。

这个设计能正确重建 OpenAI 风格的 assistant-with-tool-calls 消息；但从长期事件语义看，空 `assistant_message` 容易被其他投影误认为“AI 说过的一句话”。issue 021 排查时已经看到这类空 assistant message 会污染 recall 结果并挤占 `limit`，当前通过 recall 过滤规避。

## 根因初判

OpenAI / Anthropic 的 tool-use 协议都要求保留“assistant 发起工具调用”这一帧，再把 tool result 回填给模型继续生成。项目当前实现把这帧复用为 `assistant_message(content="") + tool_call_request`，属于可运行但语义偏弱的建模方式。

更准确的领域语义应该区分：

- 用户可见对话内容：user / assistant 实际说出的文本。
- 协议 transcript：assistant tool-call carrier、tool result 等用于 LLM replay 的结构化事件。

当前两者都混在 `assistant_message` / `tool_call_request` 的事件组合里，要求每个下游投影都记得跳过空 assistant，容易漏。

## 影响与判断

短期影响可控：

- LLM replay 需要这类 carrier，不能简单停止落盘。
- `recall_past_chats` 已过滤空 assistant，避免继续影响“最近聊了啥”体验。
- `memory_feed` 已只保留有文本的 user / assistant 话语，不会把空 assistant 入记忆。

但从长期维护看，这是 session schema / projection 边界的技术债。未来新增摘要、导出、可视化、审计或其他历史投影时，如果直接把 `assistant_message` 当“AI 说过的话”，仍可能再次踩坑。

暂不立即重构，因为它会触及 session event schema、`Session.messages` 投影、上下文裁剪/摘要对 tool group 的完整性假设，以及历史兼容策略。

## 可选修复方向

- 新增更语义化的事件类型，例如 `assistant_tool_call` 或 `assistant_protocol_message`，专门承载 tool-call assistant frame。
- 或调整事件模型为 turn-level 结构：一个 assistant turn 内同时包含可见文本和 tool calls，投影到 OpenAI 时再展开。
- 保留现有事件向后兼容，但在所有“用户可见历史”投影中集中复用同一套过滤 helper，避免各模块重复判断空 assistant / partial assistant / tool result。
- 若未来做 schema 迁移，提供旧事件到新事件的只读兼容投影，不急于改写用户已有 JSONL。
