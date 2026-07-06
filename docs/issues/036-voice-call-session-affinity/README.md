# 036 · 语音通话会话归属依赖不稳定的跨窗口内存态

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-07-06
- **关联需求**：007-voice-call

## 现象

语音通话启动时，有时会续上用户预期的历史会话，有时会静默创建新的 voice session，并出现在普通对话列表中。用户没有显式点击"新对话"，但列表里会多出类似"会话 2026-07-06 19..."的条目。

日志显示两类行为都存在：

- 旧行为：`activeChatSessionId` 有值，`resolvedSessionId` 指向历史会话，语音通话续用该 session。
- 异常行为：`activeChatSessionId:null`、`resolvedSessionId:null`，`voice_bridge` 收到 `has_session=false` 后调用 `agent_bridge.create_session(channel="voice")`，例如 2026-07-06 18:33 和 19:04 两次通话分别创建了 `88247401-...` 与 `c3c41515-...`。

## 根因初判

当前语音启动依赖前端 `voiceStore.activeChatSessionId`：

1. Chat 页面从 `currentSessionId` 广播 `voice://active-chat-session`。
2. Voice store 监听事件并保存到内存态 `activeChatSessionId`。
3. `requestStart()` 用 `req.sessionId ?? activeChatSessionId ?? undefined` 决定是否续用历史会话。
4. 若该值为空，`voice_bridge` 会自动创建新的 voice session。

这个链路不是权威状态，且存在重启、窗口挂载顺序、auto-restore 时机、隐藏 WebView 是否仍存活等竞态。项目已有 `lastChatSessionId` 持久化，且 `GET /v1/sessions` 按 `updated_at` 倒序返回最近活跃会话，但语音启动当前没有直接使用这些权威/持久来源作为 fallback。

## 影响与判断

影响用户对"本次通话属于哪段对话"的理解，并会污染普通会话列表。它还会造成模型归属误判：新建 session 会使用当前全局默认模型，而历史 session 会保留自身 `current_model`，用户可能误以为旧会话自动切了模型。

暂不在当前 Kimi provider 接入需求中修复，因为它涉及语音入口交互、会话列表展示策略、voice session 是否应混入普通 chat session、以及模型跟随策略，需要单独设计。

## 可选修复方向

- 语音启动时若没有显式 `sessionId`，从持久化 `lastChatSessionId` 或 `GET /v1/sessions` 的最近活跃非归档 session 中选择默认会话，并在使用前校验存在。
- 在语音入口提供明确选择：接到最近会话、选择历史会话、新建语音会话。
- 如果新建语音会话，明确标记为 voice-created session，并决定是否默认隐藏在普通对话列表之外，或要求用户显式保存/转为普通对话。
- 明确模型策略：接到历史会话时保留该 session 当前模型；新建会话时使用当前全局默认模型；是否让历史会话跟随全局模型另行设计。
