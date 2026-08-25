# 100 · Reminder 通知策略与统一会话历史 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 扩展 Reminder、occurrence 与逐投影数据模型，完成旧 SQLite additive migration
- [x] 让 create/add_reminder 保存宿主注入的来源 session，保持模型可见工具契约不变
- [x] 持久化 IM binding 最近真实交互时间并提供稳定路由候选
- [x] 实现 occurrence 持久化会话与通知模式的一次性冻结
- [x] 重构 Reminder 编排：conversation 事实、同 session Agent run、QQ/桌宠独立投影
- [x] 新增 Reminder 通知模式 settings provider 与 runtime 装配
- [x] 新增 Tauri/TypeScript 设置字段和设置页“提醒”选择器
- [x] 补齐 Python、Rust、TypeScript 定向测试
- [x] 运行项目统一检查并记录结果
- [x] 完成用户验收
- [x] 修正持久化会话排序为 session 最近真实用户消息，并覆盖旧 binding 数据

## AC 验证

- [x] 创建 Reminder 只保存未来计划和 Reminder 自身来源会话
- [x] 多 IM binding 到期时，在其有效 session 中按最近真实用户消息选择；offline 不影响持久化选择
- [x] 无有效 IM binding 时回退 Reminder 来源；两者缺失时不猜其他 session
- [x] occurrence 的持久化 session、binding 与通知模式只冻结一次
- [x] Reminder 卡片、Agent run 与回复始终在同一选定 session，并保持 conversation 可见
- [x] 智能模式只控制 QQ/桌宠额外投影，不改变持久化历史
- [x] 所有位置模式同时投影选中 QQ 与可用桌宠，不广播其他 binding/session
- [x] QQ 固定提醒、QQ Agent 回复与桌宠状态分别持久化且互不覆盖
- [x] 重试、binding_changed、unknown 和 Agent failure 不重复卡片、生成或外部发送
- [x] 设置默认 smart、可持久化，设置页文案与需求一致
- [x] QQ 新路径的 Reminder 与 Agent 回复可在会话历史和搜索中恢复
- [x] 自动化测试不调用真实 LLM、QQ 或外部付费服务
- [x] 项目统一检查脚本通过
- [x] 用户明确确认验收完成
- [x] 桌面与 IM 用户消息统一参与候选会话排序；Agent、Reminder 和 binding 更新时间不参与
- [x] 周期 Reminder 的新 occurrence 会在其他候选会话出现新用户消息后重新定位

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-23 | 确认 requirement 与 design，进入实现阶段 | 用户确认冻结模式、保留同会话 Agent 批处理、binding 失效不改投，并授权直接实现 |
| 2026-08-23 | 完成 Todo/Reminder additive schema、来源 session、occurrence 冻结上下文和逐投影状态 | Todo/工具定向测试 22 项通过；迁移、冻结和独立状态已有单测 |
| 2026-08-23 | 完成持久化 IM interaction recency、唯一会话选择和 Reminder 编排重构 | Bridge/IM/assembly 定向测试 71 项通过；Reminder 编排测试连续 5 轮通过 |
| 2026-08-23 | 完成 smart/all 设置的 Rust、TS、Bridge provider 与设置页 UI | frontend lint/typecheck、105 个 Vitest 文件 604 项测试通过；Rust 215 项单测与 release check 通过 |
| 2026-08-23 | 收紧外部通知 at-most-once 边界 | QQ/桌宠发送前先原子落 `unknown/send_in_progress`；崩溃或结果不确定时不自动重发，定向测试覆盖单 attempt claim |
| 2026-08-23 | 项目统一检查全部通过 | backend：2119 passed / 4 skipped；frontend：604 passed + production build；Rust：216 passed / 1 ignored + release check；未调用真实 LLM、QQ 或外部服务 |
| 2026-08-23 | 完成真实 Tauri 设置页视觉与交互验收 | 当前 feature worktree 的真实 WebView DOM、CDP 截图与 Windows 桌面同步截图均确认布局、下拉选项及两套帮助文案；切换为“所有位置”后完整重启桌面进程仍保留选择，验收结束后已恢复默认“智能选择（推荐）”并核对落盘 |
| 2026-08-23 | 用户确认验收并授权合入主干、推送远端 | 需求状态更新为 `COMPLETED`，进入 Guide 维护与合入流程 |
| 2026-08-23 | rebase 最新 `origin/main` 后重跑项目统一检查 | backend：2126 passed / 4 skipped；frontend：604 passed + production build；Rust：216 passed / 1 ignored + installer helper tests + release check；三个 stage 均通过 |
| 2026-08-25 | 临场修复持久化会话排序：改用 session active `user_message`，移除 binding 更新时间 fallback，保留 occurrence 单次冻结 | 聚焦回归 14 项通过；backend：2197 passed / 4 skipped；frontend：619 passed + production build；Rust：216 passed / 1 ignored + installer helper 6 项 + release check；未调用真实 LLM、QQ 或外部服务 |
