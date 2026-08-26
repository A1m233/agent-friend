# 106 · 消息文本引用与跨通道统一引用 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 新增统一 `message_references` 领域模型、校验与模型可见 renderer。
- [x] 让 `Message`、Session 事件、Conversation、预算、摘要、检索和记忆观察一致携带引用。
- [x] 接入 AG-UI 新消息、编辑重发、幂等和 quote-only 输入协议。
- [x] 接入 QQ 单条整消息引用解析、显式 unavailable 降级与持久别名索引。
- [x] 前端 ChatMessage / DraftWorkspace / send / edit / restore 携带引用。
- [x] 实现同一文本块选区捕获、Codex 式聚合入口、shadcn Popover 多条列表和独立删除。
- [x] 实现历史引用展示、虚拟列表来源跳转、精确高亮与安全降级。
- [x] 补齐 Agent、Session、Bridge、QQ 和 frontend 确定性测试。
- [x] 运行项目 `scripts/check/run.*` 的 backend / frontend 门禁并处理回归。
- [x] 按用户授权尝试真实桌面视觉验收；当前机器由需求 105 的 Tauri runtime 占用，按运行态治理记录为 `not verified`，未抢占其窗口。
- [x] 完成固定 QQ quote payload 的 C2C、直接原文 / 用户与 Bot 别名恢复 / unavailable smoke；当前未取得脱敏真实 payload，且既有 QQ adapter 明确不接收 group inbound，作为外部 / 既有能力缺口记录。

## AC 验证

- [x] AC-1 / AC-2：用户与 Agent 文本片段引用（DOM 选区与角色投影自动化覆盖；真实桌面视觉未验证）。
- [x] AC-3：多引用聚合、顺序稳定和独立删除。
- [x] AC-4：纯引用发送。
- [x] AC-5：发送后完整引用展示。
- [x] AC-6：来源回跳和准确片段高亮（UTF-16、唯一匹配和多义拒绝自动化覆盖；真实长列表视觉未验证）。
- [x] AC-7：会话关闭重开后恢复引用。
- [x] AC-8：结构化上下文能区分来源角色和重复文本（统一 renderer 确定性覆盖；未额外调用真实 LLM）。
- [x] AC-9：桌面与 QQ 复用统一持久化和上下文投影。
- [ ] AC-10：QQ C2C fixture 已覆盖；真实 QQ payload 未取得，且既有 adapter 不接收 group inbound，因此群聊场景未验证。
- [x] AC-11：QQ 纯引用、索引恢复和 unavailable 降级。
- [x] AC-12：桌面历史正确展示 QQ whole-message 引用。
- [x] AC-13：普通消息、复制、编辑、附件和会话恢复不回归。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-27 | 用户明确授权“开始实现”；创建阶段 3 进度文件并冻结任务与 AC 映射。 | 分支 `feature/106-message-text-quoting`；方案状态 `CONFIRMED`。 |
| 2026-08-27 | 完成统一引用模型、桌面 AG-UI / 编辑重发、QQ whole-message 归一化与别名恢复、Codex 式多引用入口和来源回跳。 | 通用浮层使用 shadcn Popover；TDesign 仅保留消息内容和发送器边界。 |
| 2026-08-27 | 补齐引用模型、上下文投影、AG-UI、QQ、草稿恢复、历史投影与 UTF-16 选区/高亮回归测试。 | 快速集：backend 28 passed；frontend 42 passed。 |
| 2026-08-27 | 完成 backend / frontend 阶段门禁。 | 初始 feature 基线：Backend `2235 passed, 4 skipped`；Frontend `633 passed`。合入最新 `origin/main` 后复验：Backend ruff + mypy + `2277 passed, 4 skipped`；Frontend lint/token/provenance + `635 passed` + production build。构建仅有既存 CSS Highlight / chunk-size warning。 |
| 2026-08-27 | 完成 Rust / Tauri 门禁。 | `cargo fmt --check`、222 项 Rust test（221 passed / 1 ignored）、`cargo check --release` 通过；仅有既存 dead-code warning。 |
| 2026-08-27 | 按用户授权执行桌面验收 preflight。 | `desktop-visual doctor` 通过，但共享 runtime 属于 `worktrees/2040` 的需求 105；未 takeover，结论为 `not verified`。 |
| 2026-08-27 | 用户明确要求在普通验证节点不暂停，并授权收尾后合入 `main`、推送远端和构建 local-test。 | 需求按用户验收决定进入收口；AC-10 的真实 payload / group 既有缺口保留为明确 residual risk，不伪造通过。 |
| 2026-08-27 | 将最新 `origin/main`（含需求 105 QQ 附件文字合并）合入 feature，并验证与引用 resolver 的组合链路。 | 无源码冲突；同步新增 resolver 参数的测试桩后，QQ coalescing / runtime / quote / assembly 交叉集 `55 passed`，随后 backend / frontend 全量复验通过。 |
