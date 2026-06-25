# 021 · recall recent summary quality is unstable

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-19
- **关联需求**：020-engine-tool-conversation-history、022-im-channel-and-qq-adapter

## 现象

真实 IM 会话 `im:qq:demo-user-2` 中，用户问：

> 你还记得我们最近聊了啥吗

本轮确实触发了 `recall_past_chats`，但结果质量不符合“最近聊天摘要”的理想体验：

1. 第一次工具调用参数为 `{"since": "最近", "limit": 10}`，工具返回错误：`时间格式没看懂：无法识别的时间表达式：'最近'`。
2. 随后 LLM 再次调用 `recall_past_chats`，只传 `{"limit": 10}`，工具按时间倒序返回最近 10 条消息事件。
3. 返回结果混入当前轮内容，包括本轮用户问题“你还记得我们最近聊了啥吗”、assistant 调工具前过渡话“让我翻翻记忆看看……”，以及一条空 assistant message。
4. 因为当前 session 里最近的有效旧内容只有“今天下了好大雨”，最终 assistant 回答“最多就聊到说你那儿下了大雨”，从当前数据看可解释，但作为“最近聊了啥”的摘要体验偏窄。

进一步观察到，历史事件里 assistant 消息存在 `partial=false` 和 `partial=true` 重复落盘现象，会挤占 `limit` 名额，让泛查询更容易只拿到少量真实话题。

## 根因初判

这是多个问题叠加导致的召回质量缺陷，不是工具未注册问题。当前拆解如下：

- 工具契约没有把“明确时间下界”和“开放式近期意图”说清楚。`since` 的 schema 只举了「3 天前 / 上周 / 上月 / 去年」等例子，但没有明确「最近 / 近期 / 这阵子」这类泛近期表达不应作为硬边界传入；LLM 把用户原词 `最近` 塞进 `since` 是自然失败模式，不应简单归因于模型没读工具定义。
- `recall_past_chats` 不知道当前 turn 边界，会扫描到本轮已经写入的 user / assistant 事件；这与 issue 020 同源，已由 issue 020 的 current-turn boundary 过滤修复。
- 工具目前以“消息事件”为单位按时间倒序截断，而不是以“话题 / turn / session 摘要”为单位组织结果。
- session 落盘里存在 assistant partial 重复事件，增加噪声并挤占 `limit`。进一步排查后判断：`partial=true` 是被中断的部分回复，不应作为 recall 里的稳定历史发言；空 assistant message 多数是 tool-call 轮的协议承载，也不应作为 recall 命中。
- 对“最近聊了啥”这类无关键词泛查询，当前工具只返回片段列表，缺少专门的摘要型检索/聚合策略。

## 影响与判断

影响 `020-engine-tool-conversation-history` 和 `022-im-channel-and-qq-adapter` 的核心体感：用户已经看到 agent 会调用会话工具，但回答仍像“只记住了很近的一点点”，容易被感知为记忆能力弱或召回不完整。

严重度定为 `medium`：工具调用链路可用，且当前 IM session 里“下雨”确实是本轮前最近的有效旧内容；但对“最近聊了啥”这种高频、自然的用户问法，结果质量还不稳定。

暂不在本 issue 内立即修复，因为它横跨时间表达解析、当前 turn 过滤、partial 事件落盘和召回结果组织策略，适合作为后续一次独立修复来闭环。

## 可选修复方向

- 明确工具定义和 runtime prompt：`since` / `until` 只用于明确时间边界；“最近 / 近期 / 这阵子”这类开放式近期泛查询应省略 `since`，只用 `limit` 按时间倒序取最近片段。
- 修 issue 020：给工具调用注入当前 session / turn 边界，过滤当前轮 user 问句、assistant 过渡话和空消息。（已处理）
- 修复 `Conversation.stream()` 在消费方拿到 `TurnDone` 后关闭 generator 时误落 `partial=true` assistant 的问题，避免未来继续产生已完成回复的 partial 重复。
- 在 `recall_past_chats` 扫描时过滤 `partial=true` assistant 和空 assistant message，避免既有历史噪声继续挤占 `limit`。
- 对无 query 的时间范围泛查询增加“按 turn 聚合 + 去重 + 摘要友好排序”，避免简单按单条 message 截断。
- 增加真实或 fake LLM smoke case：用户问“你还记得我们最近聊了啥吗”时，断言工具参数可用、结果不包含当前轮、且能覆盖最近多个历史话题。
