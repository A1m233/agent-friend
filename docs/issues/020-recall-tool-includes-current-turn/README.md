# 020 · recall_past_chats includes the current turn in results

- **状态**：resolved
- **严重度**：medium
- **发现日期**：2026-06-19
- **关联需求**：020-engine-tool-conversation-history

## 现象

在 issue 019 的真实 LLM smoke 中，时间范围型泛查询会调用 `recall_past_chats`，但工具结果会混入当前轮刚写入的 user 问句，以及 LLM 调工具前已经输出的过渡话。

复现脚本：

```bash
uv run python docs/issues/019-im-recall-tool-not-triggered/llm_recall_smoke.py --case recent_summary_no_topic,today_summary,last_few_days_user_said
```

已观察到的例子：

- `recent_summary_no_topic` 问“我们最近聊了啥？”时，工具返回里包含本轮“我们最近聊了啥？”以及“让我回忆一下最近的聊天内容。”
- `today_summary` 问“今天我跟你说过什么来着？”时，工具返回里包含本轮问题、上一轮 smoke 的问题，以及 assistant 的过渡/总结文本。
- `last_few_days_user_said` 使用 `said_by="you"` 后能过滤 assistant 过渡话，但仍会包含当前轮用户问题。

## 根因初判

`Conversation.stream()` 在调用 LLM 前会先把当前 user input 追加到 session；LLM 如果随后调用 `recall_past_chats`，该工具扫描同一个 `SessionStore` 下的所有 session events，当前 session 已经包含本轮 user 事件，且工具调用前的 assistant 文本也可能已作为事件进入 session。

当前 `ConversationHistoryTool` 不知道“本次工具调用发生在哪个 session / 哪个 turn 边界之后”，因此无法排除当前轮事件。

## 影响与判断

这不是工具未注册问题，也不影响“能否跨 session 找到历史”的基本能力；但会影响“最近聊了啥 / 今天说过什么”这类时间范围型泛查询的结果质量。

用户会看到 agent 把刚刚问的问题也当作“历史回忆”来总结，体感上像是在凑数或自我引用。严重度定为 medium：核心能力可用，但结果干净度不足。

## 可选修复方向

- 给 `recall_past_chats` 注入当前调用上下文（current session id + 当前 user event ts / uuid），扫描时排除当前 turn 及之后的事件。
- 或在 `Conversation` 调工具前，把 current turn 边界作为隐藏参数传给工具执行层，由工具 registry / invoker 统一提供。
- 对时间范围型泛查询增加专门测试：断言结果不包含当前用户问题和 tool-call 前的 assistant 过渡话。
- 短期可在工具 description / runtime prompt 中提示 LLM 不要把当前问题当作历史；但这只能缓解最终表达，不能解决工具结果污染。

---
已在 commit `TBD` 修复。
