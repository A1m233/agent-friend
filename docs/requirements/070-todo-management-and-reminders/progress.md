# 070 · Todo 管理与可选提醒 - 进度

## 总体状态

COMPLETE

## 任务列表

- [x] 实现 Todo、Reminder、Occurrence 领域模型与 SQLite 持久化
- [x] 实现一次性和基础周期规则、时区/DST/月末语义
- [x] 实现 TodoService 生命周期、revision 冲突和 Reminder 停用规则
- [x] 接入 `todo_manage` Agent 工具和稳定结果契约
- [x] 接入 Bridge Todo REST API 与 runtime 装配
- [x] 实现 scheduler、claim、错过合并、恢复和幂等投递
- [x] 实现桌面/IM/桌宠单目的地策略和可诊断 presence snapshot
- [x] 扩展 system-trigger presentation，接入固定 Reminder session event
- [x] 改造 IM outbound receipt，并接入默认 Agent 回应
- [x] 实现 Tauri Windows/macOS/Linux presence adapter 与上报
- [x] 实现前端 `ReminderBlock` / `ReminderCard` 投影
- [x] 补充跨平台 presence smoke 入口和脚本文档
- [x] 完成受影响测试、`scripts/check` 与验收证据整理

## AC 验证

- [x] Todo 可跨重启和会话创建、列出、修改
- [x] Todo 状态及完成、取消、重新打开语义正确
- [x] 多条一次性 Reminder 至多触发一次
- [x] 基础周期 Reminder 和结束条件正确
- [x] 同一 Todo 的多 Reminder 可独立管理
- [x] Agent 回显使用规范化绝对时间、时区与周期
- [x] 到期无需用户消息即可进入主动出口
- [x] 桌面对话先展示固定卡片，再默认执行 Agent 回应
- [x] IM 先展示固定事实，再默认执行 Agent 回应
- [x] 桌宠降级只展示固定事实
- [x] 同一 occurrence 至多一个成功目的地
- [x] 桌面 30 秒、聚焦、锁屏、休眠和 unknown 策略正确
- [x] Windows 真实桌面状态、空闲阈值与投递/降级链路得到验证
- [x] 诊断不采集原始输入内容
- [x] 重启恢复和错过合并正确
- [x] 取消/更新竞态与幂等正确
- [x] Tool 与未来 UI 复用同一后端边界
- [x] 自动化测试覆盖核心逻辑且默认不调用真实外部服务
- [x] 项目统一检查脚本通过
- [x] 用户明确确认按 Windows-only 范围验收并继续收尾

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-07-28 | Phase 3 启动；按已确认 requirement/design 建立实现与 AC 跟踪 | 当前分支 `feature/070-todo-management-and-reminders` |
| 2026-07-28 | 完成 Todo/Reminder/Occurrence 数据层、周期计算、TodoService 与 `todo_manage` | 定向测试 37 项通过；覆盖 DST、月末、错过合并、revision 和工具契约 |
| 2026-07-28 | 完成 Bridge REST/runtime、scheduler、单目的地 presence、IM receipt、固定事件与 Agent 增强链路 | 相关后端定向测试 109 项通过；固定事实投递与 Agent response status 分离 |
| 2026-07-28 | 完成桌面对话 ReminderBlock/ReminderCard、presentation 分流与跨平台 presence 诊断脚本 | 前端投影/分流定向测试 23 项通过；真实桌面 presence 尚待用户授权后验收 |
| 2026-07-28 | 三段统一门禁通过 | backend：ruff + mypy + 1184 passed / 2 skipped；frontend：446 passed + production build；Rust：140 passed / 1 ignored + release check |
| 2026-07-28 | 补齐取消/更新与到期投递竞态 | occurrence 增加可迁移失效标记并在外部出口前复核；新增 3 项回归测试后后端全量门禁仍通过 |
| 2026-07-28 | 完成 Windows 真实桌面端提醒验收 | 真 Tauri + Bridge + CDP 运行：窗口隐藏时安全降级桌宠；窗口可见、聚焦且 30 秒内有输入时选择当前长会话；跨过 30 秒阈值后不再把桌面判为可达；一次性提醒在当前会话先渲染固定 Todo 卡片，再触发 1 次真实 Agent 回应，occurrence 为 `desktop / delivered / completed`。证据位于 `.tmp/desktop-acceptance/`（本地、不入库） |

## 平台验收边界

- Windows 已实测窗口隐藏、显示/聚焦、停止输入跨越 30 秒阈值、桌宠降级、IM online 快照，以及桌面对话固定卡片 + Agent 回应链路。
- 用户明确将本期真实平台验收范围收窄为 Windows；macOS 与 Linux 不再作为本期验收或交付门槛，现有兼容实现保持不变。
