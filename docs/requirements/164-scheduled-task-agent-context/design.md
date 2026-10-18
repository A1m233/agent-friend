# 164 · 定时任务来源上下文 - 技术方案

## 状态

CONFIRMED

## 需求文档

[requirement.md](./requirement.md)

## 现状分析

调度 runner 将任务指令作为普通请求提交，并通过 request_source 保存任务名称与标识。Session 的 user_message 投影只取正文、附件、引用和事件 meta，未传递 payload 中的来源。当前输入又由 ContextManager 重新构造，因此只修改历史投影不足以修复首次请求。

模型历史和 rollout 独立保存已装配输入；旧 rollout 不会自动因业务事件拥有来源而刷新。摘要转录和任务锚点也有各自的渲染入口，需要使用相同来源事实。

scheduled_task_manage 已注册且有 CRUD/history 实现。create 必须提供 target_session_id，但自然语言历史查询不返回会话 ID，用户明确选择当前会话时缺少可直接使用的输入表达。

## 方案设计

### 按消息保存和渲染来源

- 保留 user_message.payload.request_source 为事实源；调度 runner 补充 trigger_kind、scheduled_at_utc，名称取当次任务快照。
- 在 agent/session_requests 下维护小型纯函数：规范化允许的来源字段、生成模型可读说明。只识别宿主提供的 scheduled_task；不解析正文猜来源。
- Session 投影将规范化来源带到 Message.meta。模型输出序列化在对应用户消息前缀渲染宿主来源说明，不修改业务正文、UI 内容或消息角色。
- 标记明确为本条消息的来源，而非全会话当前模式。工具续接与后续历史继续使用同一条消息的来源，不在每次调用追加新消息。
- task_id、occurrence_id、task_name、trigger_kind、scheduled_at_utc 各自保留语义；字符串编码转义，正文中的同名保留标记转义，任务名不能闭合宿主块或成为指令。
- 提供稳定的解释：消息来自已保存任务，不等同于用户刚手动重发正文；遵守已有用户约束。正常定时任务仍可执行，不把标记变成一律停止的命令。

### 首次请求、恢复与压缩

- 当前输入在预算评估和 provider 装配前绑定实际接受的用户事件来源；不能把旧 current_input 标记错误重绑到新事件。
- 旧模型历史按业务事件 UUID 或经过内容核对的来源引用补齐元数据，不篡改旧落盘 transcript，不从当前任务表倒推历史。
- 已有精确 rollout 仍由现有校验恢复；来源补齐导致请求内容变化时走现有 rewrite/cache rebase，不能继续宣称 wire 前缀完全相同。
- 摘要转录使用同一来源 renderer；摘要契约要求保留定时来源与仍有效的用户限制，避免将宿主来源解释为新授权；summary_prompt_version 更新为 5，旧 checkpoint 格式继续兼容。
- 当前任务锚点携带来源，重建锚点时从其绑定的原始事件恢复。来源元数据不改变用户正文；原有正文完整性校验继续执行。
- 旧记录缺 trigger_kind 或时间时只显示已保存字段，不猜测。已不可恢复到具体消息的旧摘要不能凭空补出来源；对新压缩路径验证输入及锚点保真。
- 部分旧 rollout 曾把历史 current_input 引用一并重绑到后来的事件；恢复时要求引用唯一且正文吻合，重复或冲突引用不猜测补齐。新输入只绑定最新一条，避免继续产生这种歧义。

### 当前会话创建入口

- create 增加 target_current_session=true，与 target_session_id 严格互斥，二者必须选一；不以省略目标表示当前会话。
- 仅在用户明确选择当前会话时使用该参数；宿主通过现有隐藏的 __agent_friend_current_session_id 解析真实 ID。
- 继续调用现有 ScheduledTaskService.create 和目标验证器，沿用会话可用性、权限与任务持久化规则。没有可信当前会话时返回明确可恢复错误。
- 修改既有任务继续使用明确 ID，不借此次创建入口扩张跨会话发现、创建会话或授权能力。

### 涉及文件与责任

| 范围 | 责任 |
|---|---|
| agent_bridge/scheduled_tasks/runner.py | 提交当次触发事实 |
| agent/request_context/ | 来源规范化、模型渲染 |
| agent/messages.py、sessions/session.py、conversation.py | 业务投影、当前输入、模型请求与旧历史补齐 |
| agent/context/task_anchor.py、checkpoints.py、summary_prompt.py | 压缩资料与任务锚点来源保真 |
| agent/tools/builtin/scheduled_task_manage/ | 当前会话创建参数、校验与错误 |
| agent/tests、agent_bridge/tests | provider 输入、恢复、摘要与工具调用链回归 |

## 影响分析

- 不改桌面协议字段既有含义和 UI 文本，新增来源字段保持可选；不启动视觉验收。
- 不新增依赖、动态导入或资源收集方式，按 frozen-runtime-packaging 的 standard 级处理。
- 来源参与实际渲染与预算，避免仅在最后发送时拼接造成预算/缓存/审计不一致。
- 不增加调度状态、不修改真实用户任务或会话，不运行真实 MAA。
- 确定性测试使用 fake provider 和临时数据库；真实 LLM 行为验证另行授权，未执行时保留缺口。
- 使用 scripts/test 与 scripts/check 的标准入口；统一检查按 backend、frontend、rust 分阶段执行。

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|---|---|---|
| 2026-10-18 | 用户确认方案并授权实现；补齐消息来源和当前会话创建入口 | 首次实现 |
