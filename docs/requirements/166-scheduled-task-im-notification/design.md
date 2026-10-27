# 166 · 定时任务 IM 结果通知 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

- `ScheduledTaskService`、REST 和 `scheduled_task_manage` 已共用任务存储；任务执行配置以 occurrence snapshot 固定。
- `ScheduledRunTracker` 记录运行终态，调度前失败和启动恢复也会在同一 occurrence 表落终态。
- `IMRuntime.presence_snapshots()` 能提供全部 IM 绑定及在线状态，`send_proactive()` 校验绑定并调用现有 provider；发送结果使用 `OutboundSendResult`。
- Todo 的固定提醒与 Agent 回复已有发送前原子占位及回执持久化，相关操作分别在 `TodoStore` 和 `ReminderDeliveryOrchestrator`。其表按提醒及固定投影类型唯一，不能直接承载多目标任务通知。
- 桌面通知有独立的文本截取与生成策略，不作为本次任务完整结果的来源。

## 方案设计

### 任务配置

任务及执行快照增加 `notify_bound_im: bool = false`。任务表增量加列，旧快照缺失字段按 false 读取，更新时未传字段保留当前值。REST 与管理工具使用同一 service，布尔值严格校验，说明只有用户明确要求时才开启。

此配置属于任务，不加入通用会话请求权限选项；不开启远程审批，不改变执行 surface、权限、模型、Skill 或队列语义。

### 公共投递流程

在现有 `protocols/im/delivery/` 下提取一次性投递 helper。调用方提供原子 claim、发送与 settle 回调，helper 统一完成 claim → send → receipt settlement。发送异常归为 unknown，失败和 unknown 不自动重试。

Todo 固定提醒与 Agent 回复改用该 helper，保留现有 Todo 表、批量合并回复、固定提醒成功后才发增强回复、presence 策略和桌宠逻辑。定时任务也调用该 helper，继续使用 `send_proactive()` 与 adapter 错误分类。公共 helper 不依赖 Todo 或 Scheduled 类型，不引入另一套通道、凭据或平台 SDK。

### 任务通知记录

在现有 scheduled tasks SQLite 内增加通知计划与逐目标投递记录，由 `scheduled_tasks/delivery.py` 管理。开启通知的 occurrence 创建时在同一事务登记计划，关闭与旧 occurrence 不登记，避免升级追溯发送。

- 计划以 occurrence id 唯一，保存准备状态、正文及跳过/失败原因。
- 目标记录以 occurrence id + destination id 唯一，保存状态、回执、错误及尝试次数。
- 计划仅处理已终态 occurrence，成功、失败、中断、拒绝可发送；取消、跳过、过期记 skipped。
- 准备时使用本次运行最终回复并固定正文及当前目标集合；无绑定记 skipped。
- 目标发送前原子从 pending 改为 unknown/send_in_progress，再调用外部发送。回执写入 delivered/failed/unknown。
- 计划未准备或目标仍 pending 时，重启可继续首次处理；unknown、failed、delivered、skipped 不重发。计划完成后不因新增绑定重新展开目标。

数据库只记录首次投递进度，不提供自动重试队列或补发 API。执行终态不因通知改变。

### 完成接入与生命周期

复用 tracker 的轮询和启动恢复入口，通知组件以单个后台工作线程处理持久化待投递记录。tracker 只唤醒组件，不做网络 I/O；后台批次有界，单目标超时沿用发送入口。先恢复 occurrence，再启动通知处理，可覆盖正常终态、提交前失败及重启中断，且不新增调度器。

关闭时先停止接受新的投递，结束当前有界尝试，再停止 IM；不得在已经关闭的数据库或通道上继续发送。未尝试记录保留 pending，尝试中断记录保留 unknown。

### 结果与目标

通过现有业务会话读取路径取得持久化事件，按实际 run id 过滤，只选非 partial 的最终回复并应用已有 phase 修正。不得恢复 provider reasoning/transcript 或扫描取“最后一条回复”代替运行关联；无法确认最终回复时不把 commentary 当结果。无正文时使用确定性状态文案，不新增模型调用。

目标从 `presence_snapshots()` 按本次实际执行会话过滤，保留离线绑定以准确记录 provider_unavailable。投递前在 IM 事件循环实际 dispatch 处再次校验绑定，已解绑或切换目标不发送；此检查不能撤回已交给外部平台的消息。绑定查验的改动放在公共发送入口，原提醒也受益。

### 界面与工具

- 任务编辑页在目标会话附近复用已有 Switch，说明发送到全部当前绑定，未绑定时跳过；默认关闭。
- 任务详情展示开关，运行历史在原执行结果下显示独立的通知状态和各目标结果，目标标识脱敏展示。
- API 和管理工具返回相同通知投影，history 能查询发送失败原因。已有任务更新省略该字段不会重置开关。

### 涉及文件

| 范围 | 改动 |
|---|---|
| `agent/src/agent/scheduled_tasks/{models,service,store,codec}.py` | 开关、快照、迁移、通知查询投影 |
| `agent/src/agent/scheduled_tasks/delivery.py` | 同库通知计划及原子投递记录 |
| `agent/src/agent/tools/builtin/scheduled_task_manage/tool.py` | 开关参数与历史结果 |
| `agent_bridge/src/agent_bridge/protocols/im/delivery/` | 从提醒提取一次性投递流程 |
| `agent_bridge/src/agent_bridge/reminders.py` | 两类 IM 提醒使用公共流程 |
| `agent_bridge/src/agent_bridge/protocols/im/runtime.py` | 实际 dispatch 前校验现有绑定 |
| `agent_bridge/src/agent_bridge/scheduled_tasks/` | tracker 唤醒、结果读取及后台投递 |
| `agent_bridge/src/agent_bridge/{assembly,app}.py` | 装配与生命周期 |
| `agent_bridge/src/agent_bridge/routes/scheduled_tasks.py` | REST 参数及历史投影 |
| `frontend/src/services/api/scheduledTasks.ts`、`frontend/src/pages/scheduled-tasks/` | 类型、开关和历史结果 |

## 影响与验证

- 不新增依赖、资源或动态导入，先按 standard 业务逻辑分类；实际 diff 若触及打包高风险输入再按项目规则升档。
- 覆盖旧库及旧快照默认关闭、false 更新、多个任务独立配置、三种触发、正常/异常终态、同 run 最终结果、phase 修正、多个绑定及绑定切换。
- 覆盖重复/并发 claim、发送后落盘失败、超时 unknown、重启 pending 续发、旧历史不发送，以及慢发送不阻塞 tracker。
- 重跑原提醒、IM、调度、工具、路由及前端受影响测试，再执行项目统一检查。
- 真实 QQ 发送与桌面视觉需要单独授权；未执行如实标记，不以确定性测试代替实际接收。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-10-27 | 用户确认复用方案及两个边界，并明确要求实现 | 首次实现 |
