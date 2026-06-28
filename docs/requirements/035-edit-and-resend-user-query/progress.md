# 035 · 编辑并重发最后一条用户输入 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

## 任务列表

- [x] M35.1：session `turn_rewrite` 事件 + active projection + 单测
- [x] M35.2：memory source soft-delete + facade invalidation + 单测
- [x] M35.3：Conversation edit-resend runner + bridge SSE endpoint + 后端测试
- [x] M35.4：frontend stream API + SessionDetail active events + projection/store 测试
- [x] M35.5：ChatSenderBox 封装，Composer 迁移到底部 placement
- [x] M35.6：Message action edit 按钮 + EditMessageSender + MessageList 编辑态
- [x] M35.7：联调 edit-resend 成功/失败/刷新重开语义
- [x] M35.8：桌面视觉验收、`./scripts/check`、需求 AC 手动验收

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-28 | Phase 3 启动，创建 progress.md。 | 用户明确授权开始实现。 |
| 2026-06-28 | 完成后端 `turn_rewrite` active projection、Conversation edit-resend、memory source invalidation、bridge SSE endpoint。 | 覆盖 `agent/tests/test_conversation_edit_resend.py`、`memory/tests/test_store.py -k soft_delete_by_source_events`。 |
| 2026-06-28 | 完成前端 `editResendLatestStream`、store 乐观编辑重发、`ChatSenderBox` 封装、编辑按钮和编辑态 sender。 | 覆盖 `frontend/src/stores/conversation.test.ts`；`pnpm run typecheck`、`pnpm run lint` 通过。 |
| 2026-06-28 | 完成真实桌面端 CDP 视觉检查。 | 截图：`%TEMP%/agent-friend-chat-035.png`、`%TEMP%/agent-friend-chat-edit-035.png`；DOM：`%TEMP%/agent-friend-chat-dom-035.json`。 |
| 2026-06-28 | 尝试 `uv run mypy agent/ memory/ agent_bridge/`。 | 未作为通过项：命中既有 `llm_providers` re-export / unused-ignore 问题；本次新增的 `prior_summary_override` 类型问题已修复。 |
| 2026-06-28 | 收尾复核：补充 `edit-resend-latest` bridge route 回归测试，并跑通桌面检查与全量门禁。 | `desktop-visual doctor` 通过；最新 DOM/截图：`%TEMP%/agent-friend-chat-035-final-dom.json`、`%TEMP%/agent-friend-chat-035-final.png`；`scripts/check/run.ps1` 全绿（pytest 616 passed，frontend vitest 176 passed）。待用户明确确认验收后再标记 COMPLETED。 |
| 2026-06-28 | 用户确认验收通过，需求收尾为 `COMPLETED`。 | 本期按 `feature/035-edit-and-resend-user-query` 合入主干。 |
