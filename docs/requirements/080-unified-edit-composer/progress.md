# 080 · 统一编辑 Composer - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 将编辑目标、编辑/提交 phase 与会话清理收敛到 conversation store。
- [x] 扩展 draft store，隔离 normal/edit workspace，并支持原 asset 与新 draft 两类附件。
- [x] 删除内联编辑 Sender，增加原消息提示、Composer 聚焦和输入框外置编辑状态栏。
- [x] 让 Composer 的文字、附件、模型和语音能力统一作用于当前 active workspace。
- [x] 以 HTTP/SSE admission 为退出编辑态边界，接纳失败保留编辑稿。
- [x] 扩展 edit-resend 前端协议，发送目标 UUID 与最终附件替换集。
- [x] 在 Bridge 共享附件校验/消费逻辑，并安全解析 existing asset 与 draft lease。
- [x] 扩展 Conversation 的显式附件替换和目标 UUID 并发保护。
- [x] 补齐前端、Bridge 与 Agent 自动化回归测试。
- [x] 将用户主动停止从错误终态拆出，并持久化中性“已停止回答”状态。
- [x] 运行项目统一检查并完成真实 Tauri 桌面视觉与交互验收。

## AC 验证

- [x] AC-1 单一输入源与原地提示。
- [x] AC-2 Composer 编辑状态栏。
- [x] AC-3 内容回填与聚焦。
- [x] AC-4 取消编辑与普通草稿恢复。
- [x] AC-5 编辑后的文字与附件替换集。
- [x] AC-6 附件状态与模型兼容性。
- [x] AC-7 编辑时切换模型。
- [x] AC-8 模型切换失败恢复。
- [x] AC-9 语音输入只写入编辑稿。
- [x] AC-10 提交反馈、admission 与普通草稿恢复。
- [x] AC-11 活跃历史、刷新和上下文一致性。
- [x] AC-12 历史加载、运行、取消与切换状态互斥。
- [x] AC-13 会话与迟到异步结果隔离。
- [x] AC-14 自动化回归和真实桌面视觉验收。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-09 | 用户明确授权进入实现阶段，建立实现与 AC 跟踪清单。 | 当前分支 `feature/080-unified-edit-composer`；requirement/design 均为 `CONFIRMED`。 |
| 2026-08-09 | 完成单一 Composer、edit workspace、原位提示、编辑状态栏、admission 状态与模型/语音/附件接入。 | `frontend/lint` 通过；前端 91 个文件、516 项测试通过。 |
| 2026-08-09 | 完成 edit-resend 附件替换协议、Bridge 共享校验与 Conversation 显式替换语义。 | Python lint/typecheck 通过；相关 Agent/Bridge 47 项测试通过。 |
| 2026-08-09 | 项目统一门禁 backend/frontend/rust 三阶段全部通过。 | backend 1645 passed / 4 skipped；frontend 516 passed + production build；Rust 175 passed / 1 ignored + release check。 |
| 2026-08-09 | AC-14 真实桌面验收等待可用的当前分支 runtime lease。 | 现有 lease 属于另一 worktree 的 `feature/079-agent-friend-guide-skill`；未冒用、未停止、未 takeover。 |
| 2026-08-11 | 根据用户视觉反馈将编辑状态栏从 ChatSender 内部移到输入框边框外，随后按参考图取消纵向 gap、缩窄状态栏并弱化独立卡片感；撤销负 margin 叠压，上方状态栏移除底部圆角，ChatSender 保持原有完整圆角；附件 dropzone 收敛到输入框区域。 | 保留唯一 Composer、取消与提交状态逻辑；最新 frontend lint/typecheck 通过；真实 Tauri screenshot + CDP 计算样式确认状态栏底部圆角为 `0px`、ChatSender 顶部圆角保持 `16px`，AC-2 通过。 |
| 2026-08-11 | 将显式用户停止从失败终态拆出：Runtime cancellation 编码为带 `cancelled` 扩展结果的 `RUN_FINISHED`，前端投影为 `interrupted`；显式停止即使没有已生成正文也会持久化 partial assistant 事件，并在消息区显示中性“已停止回答”状态。 | backend 1646 passed / 4 skipped；frontend 91 个文件、519 项测试与 production build 通过；Rust 175 passed / 1 ignored 且 release check 通过。真实 Tauri WebView2 mock 状态截图与 DOM 验证通过：灰色状态点、`role=status`、无旧红色“回答已被中断”文案；未调用真实 LLM，验收后已恢复页面原状态。 |
| 2026-08-11 | 使用晚于相关源码变更启动的当前分支 Tauri runtime 完成停止回答复验，并由用户确认整体交互可收尾。 | 实际取消终态为 `cancelled / explicit_request`；当前视图稳定保留无文本 `interrupted` assistant，服务端活跃事件持久化 `partial=true` 且内容为空，刷新后不再闪退。AC-1 至 AC-14 结合自动化、桌面观察与用户手验全部通过。 |
| 2026-08-11 | 合并最新 `origin/main` 后重新执行项目三段统一门禁。 | backend lint/typecheck 通过，1666 passed / 4 skipped；frontend lint、91 个文件 519 项测试与 production build 通过；Rust 185 passed / 1 ignored 且 release check 通过。仅保留既有 CSS chunk 与 Rust unused/dead-code warnings。 |
