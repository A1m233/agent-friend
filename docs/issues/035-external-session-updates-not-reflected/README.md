# 035 · 外部入口更新会话后 chat 视图不自动同步

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-07-01
- **关联需求**：无

## 现象
当会话由非 chat 前端入口触发更新时，例如 QQ bot 收到语音消息并写入同一个 IM session，当前打开的 chat 窗不会自动看到最新消息。

实际观察到的体感是：QQ 侧已经触发并完成回复，但桌面 chat 窗仍停留在旧消息状态；用户切到其他会话再切回来后，才通过重新拉取 `/v1/sessions/{id}` 看到最新内容。

## 根因初判
当前 chat 前端把 bridge 作为真相源，但本地 store 仍是 pull cache：

- `useSessionsStore.refresh()` 只在打开历史侧栏、前端自己发送结束等局部时机触发。
- `useConversationStore.openSession()` 只在切换会话时拉取 session detail。
- QQ/IM 入口会直接写入同一份持久化 session JSONL，但没有通知 chat 窗当前 session 已变化。
- 现有 push 通道主要服务桌宠主动轮，Rust subscriber 当前只订阅 `agent_turn`，尚未承担 chat 会话变更同步职责。

因此，多入口触发后，chat 视图和持久化真相源之间会短暂或长期不一致，直到用户执行某个会触发重新拉取的操作。

## 影响与判断
影响范围包括所有非 chat 前端触发的会话更新：QQ/IM 入口、未来其他外部触发源、以及可能的主动轮或跨窗口写入。

这不是单点 UI bug，而是触发源从“前端自己发起”扩展到“多入口写入同一会话”后，前端同步模型没有随之升级。问题明显影响体感可信度：用户会误以为 agent 没收到或没回复，直到手动切换会话才看到最新消息。

暂不立即修复的原因是影响面较大：需要重新定义 push 通道和 chat store 之间的同步边界，处理当前会话刷新、会话列表排序、滚动位置、前端本地 streaming 与外部更新的去重等问题。

## 可选修复方向
先考虑做“push invalidation + pull refresh”，避免一开始就在前端做复杂增量合并：

- 后端在所有会话写入入口发出 session changed / user turn 类通知。
- Tauri push subscriber 同时订阅并分发 chat 所需的会话变更事件，而不是只服务 pet。
- chat 收到当前 session 变更时按需重拉 `/v1/sessions/{id}`；收到其他 session 变更时刷新 session list。
- 对当前用户正在阅读历史或本地正在 streaming 的场景做去抖、去重和滚动策略，避免打断体验。
