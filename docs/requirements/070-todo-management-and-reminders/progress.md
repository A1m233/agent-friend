# 070 · Todo 管理与可选提醒 - 进度

## 总体状态

COMPLETED — 用户已验收当前范围；关闭动画观感问题转 issue 041 后继续交付

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
- [x] 同一 scheduler claim 批次按实际目标会话聚合，只触发一次 Agent turn
- [x] Reminder Agent turn 使用结构化事件输入和正常工具能力
- [x] 补齐批次调度、桌面/IM 聚合、工具模式与兼容性回归测试
- [x] 重跑受影响检查并完成本轮验收证据整理
- [x] 暴露完整 Reminder schedule schema，并明确 `create.reminders` 的同调用创建能力
- [x] 移除 `add_reminder` 不需要的 revision 校验，补齐错误自修正信息与回归测试
- [x] 恢复动态运行时上下文逐次临时注入，停止新增 `model_context` 持久化事件
- [x] 兼容过滤旧 session 中已持久化的 `model_context`，不破坏原始审计事件
- [x] 为一次性 Reminder 增加 `relative_delay` 输入并在工具边界归一化为 absolute once schedule
- [x] 保证同一次模型推理的多个 Todo 工具调用共享可信时间、工具续轮重新取时
- [x] 移除 30 秒系统 idle 的桌面对话资格硬门槛，保留心跳 stale 与窗口/锁屏条件
- [x] 桌宠 Reminder 使用 Sonner 独立堆叠/排队，并按整个栈调整透明窗口尺寸
- [x] 在重启后的真实桌面会话复验“创建两个两分钟后提醒的 Todo”
- [x] 在真实 bubble window 复验多 Reminder 栈与逐条关闭；关闭跳变和动画观感转 issue 041

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
- [x] 桌面心跳新鲜度、聚焦、锁屏、休眠和 unknown 策略正确，idle 仅作诊断
- [x] Windows 验收证据覆盖真实 Reminder 创建/投递与 Sonner 短气泡栈；无 idle 门槛由确定性 presence 测试覆盖
- [x] 诊断不采集原始输入内容
- [x] 重启恢复和错过合并正确
- [x] 取消/更新竞态与幂等正确
- [x] Tool 与未来 UI 复用同一后端边界
- [x] 自动化测试覆盖核心逻辑且默认不调用真实外部服务
- [x] 项目统一检查脚本通过
- [x] 用户明确确认按 Windows-only 范围验收并继续收尾
- [x] 同一 scheduler claim 批次、同一实际目标只提交一个 Agent turn
- [x] 单条 Reminder 与多条 Reminder 共用批协议
- [x] Agent 只接收结构化 Reminder 事件，不接收 Todo 专属回答方式指令
- [x] Reminder 显式使用正常工具循环，其他 system trigger 默认仍禁用工具
- [x] IM 逐条发送固定事实后，每个目标批次只发送一条 Agent 最终回答
- [x] `todo_manage.create` 可在一次工具调用中同时创建 Todo 与一条或多条 Reminder
- [x] `relative_delay` 使用可信推理时间归一化，覆盖多提醒同锚点与 DST 回拨
- [x] 每次推理只注入一份临时时钟，工具续轮刷新，session 与模型历史不持久化动态快照
- [x] 长时间无输入但仍可见、聚焦且运行态正常的桌面对话继续具有投递资格
- [x] 同时降级到桌宠的 Reminder 不互相覆盖，可逐条关闭，阴影获得窗口内安全边距
- [x] 真实 Agent 对新建且带提醒的 Todo 直接使用 `create.reminders`，不再拆成后续 `add_reminder`

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
| 2026-08-03 | 重新进入实现阶段：改造同一 scheduler claim 批次的 Reminder Agent 回应 | 已确认 requirement/design；不改变逐条固定事实与投递状态，只聚合同目标 Agent turn，并恢复正常工具能力 |
| 2026-08-03 | 完成 scheduler 批处理、按目标聚合投递、结构化事件输入与 `SystemTriggerEvent.tool_mode` | 单条提醒走 size=1 批协议；Reminder 使用 `normal`，其余 system trigger 默认 `disabled`；批次与 occurrence 列表写入 run/session 审计元数据 |
| 2026-08-03 | 完成本轮自动化验证 | 定向 77 passed；backend：ruff + mypy + 1458 passed / 2 skipped；frontend：488 passed + production build；Rust：155 passed / 1 ignored + release check。测试全部使用 fake，不调用真实 LLM；本轮无前端视觉改动，未执行桌面视觉验收 |
| 2026-08-04 | 修复真实会话中 Todo 已创建但 Reminder 连续调用失败的 bad case | `todo_manage` 现在完整暴露 once/recurring schema，要求新建时通过 `create.reminders` 同调用提交；`add_reminder` 不再错误要求 revision，格式错误会返回预期字段 |
| 2026-08-04 | 完成 Todo 工具契约修复的自动化验证 | 定向 19 passed；backend：ruff + mypy + 1461 passed / 2 skipped。全部使用本地 service/fake，不调用真实 LLM；真实 Agent 行为待重启桌面运行态后复验 |
| 2026-08-04 | 落地逐次临时时钟与相对 Reminder | `_assemble()` 每次推理刷新可信时间，同推理工具调用共享快照；旧 `model_context` 只审计不投影；`relative_delay` 在 `todo_manage` 边界归一化为现有 once schedule，未增加 DB 迁移，也未给 `datetime_calculate` 增加当前时间查询 |
| 2026-08-04 | 完成相对时间与上下文边界定向验证 | 95 passed；ruff 与 mypy 通过。覆盖首轮/工具续轮刷新、同推理多 Todo 调用共享时间、旧 session 过滤、absolute/relative 互斥及 DST 回拨；全部使用 fake clock/fake model，不调用真实 LLM |
| 2026-08-04 | 完成本轮 backend 全量门禁 | ruff + mypy + 1466 passed / 2 skipped；本次未改 frontend/Rust，沿用 2026-08-03 已通过的对应门禁证据。真实 Agent 行为仍需重启最新桌面运行态后单独复验 |
| 2026-08-04 | 修正桌面 presence 与桌宠短气泡 | desktop eligibility 不再由 30 秒 idle 否决；Reminder 气泡改用现有 Sonner 独立堆叠/排队，普通 ambient 仍替换，透明窗口按可见栈动态调整并预留 16px 阴影 frame |
| 2026-08-04 | 完成本轮三段门禁 | backend：ruff + mypy + 1466 passed / 2 skipped；frontend：488 passed + production build；Rust：155 passed / 1 ignored + release check。未启动真实桌面视觉验收，等待用户确认或自行复验 |
| 2026-08-04 | 完成真实 Agent 创建契约复核 | 只读核对最近真实 session：同一轮两次 `todo_manage.create` 均直接携带 `reminders[].relative_delay = 2 minute`，没有后续 `add_reminder`；不新增真实 LLM 调用 |
| 2026-08-04 | 完成多 Reminder 气泡用户验收并登记残余问题 | 用户在真实 bubble window 观察多条提醒和逐条关闭，确认关闭过程存在跳变且动画观感待优化；不影响队列语义，已登记 issue 041 延后处理 |
| 2026-08-04 | 最终三段门禁通过并完成当前范围验收 | 同步最新 main 并处理契约冲突后，backend：ruff + mypy + 1481 passed / 2 skipped；frontend：488 passed + production build；Rust：156 passed / 1 ignored + release check。用户明确要求在已知动画瑕疵转 issue 后继续合入主干 |

## 平台验收边界

- 2026-07-28 的 Windows 实测覆盖完整投递链路；本轮移除 30 秒 idle 门槛由 presence 确定性测试覆盖，Sonner 多提醒栈由用户在真实 bubble window 验收。关闭动画体感瑕疵不阻塞本期，跟踪于 issue 041。
- 用户明确将本期真实平台验收范围收窄为 Windows；macOS 与 Linux 不再作为本期验收或交付门槛，现有兼容实现保持不变。
