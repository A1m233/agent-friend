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

这是多个问题叠加导致的召回质量缺陷，不是工具未注册问题：

- 时间解析器不支持“最近”这类泛时间表达，LLM 又自然倾向传入该表达。
- `recall_past_chats` 不知道当前 turn 边界，会扫描到本轮已经写入的 user / assistant 事件；这与 issue 020 同源。
- 工具目前以“消息事件”为单位按时间倒序截断，而不是以“话题 / turn / session 摘要”为单位组织结果。
- session 落盘里存在 assistant partial 重复事件，增加噪声并挤占 `limit`。
- 对“最近聊了啥”这类无关键词泛查询，当前工具只返回片段列表，缺少专门的摘要型检索/聚合策略。

## 影响与判断

影响 `020-engine-tool-conversation-history` 和 `022-im-channel-and-qq-adapter` 的核心体感：用户已经看到 agent 会调用会话工具，但回答仍像“只记住了很近的一点点”，容易被感知为记忆能力弱或召回不完整。

严重度定为 `medium`：工具调用链路可用，且当前 IM session 里“下雨”确实是本轮前最近的有效旧内容；但对“最近聊了啥”这种高频、自然的用户问法，结果质量还不稳定。

暂不在本 issue 内立即修复，因为它横跨时间表达解析、当前 turn 过滤、partial 事件落盘和召回结果组织策略，适合作为后续一次独立修复来闭环。

## 可选修复方向

- 扩展 `parse_time_expression` 支持“最近 / 近期 / 这阵子”等泛时间表达，映射到保守默认窗口（如 3 天或 7 天），并补测试。
- 修 issue 020：给工具调用注入当前 session / turn 边界，过滤当前轮 user 问句、assistant 过渡话和空消息。
- 检查 assistant `partial=true` 事件为何会与最终消息重复落盘；若只是推送/流式展示用途，应避免进入长期会话历史，或在 recall 工具扫描时过滤。
- 对无 query 的时间范围泛查询增加“按 turn 聚合 + 去重 + 摘要友好排序”，避免简单按单条 message 截断。
- 增加真实或 fake LLM smoke case：用户问“你还记得我们最近聊了啥吗”时，断言工具参数可用、结果不包含当前轮、且能覆盖最近多个历史话题。
