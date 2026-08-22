# 099 · 全局 Scheduled Agent Task 与持久调度边界

## 状态

CONFIRMED

## 1. 背景

agent-friend 已经存在多种由时间或后台状态触发的能力，但它们分别形成于不同需求，当前没有统一、可由用户管理的 Scheduled Agent Task 产品边界：

- `BedtimeSource` 在进程内自行等待固定时间并提交主动轮；
- `IdleReflectionSource` 根据空闲条件自行轮询并提交 silent turn；
- Todo Reminder 拥有独立的持久化周期、到期记录、领取、恢复和投递状态；
- `RunScheduler` 只负责已经生成的 Agent run 的优先级、并发、抢占、取消和 drain。

这些能力可以各自完成现有职责，但当用户希望“每天运行一个 Skill”“每周生成一份总结”或“在未来某个时间让 Agent 完成一项工作”时，系统仍缺少全局、持久化、可暂停、可恢复、可审计的任务定义。继续增加独立 EventSource 会复制时间等待与恢复逻辑；把非提醒任务塞进 Todo Reminder 又会让任务执行错误依赖 Todo、提醒卡片和通知目的地语义。

项目需要建立一套统一的 Scheduled Task 能力：时间触发只负责产生可追踪的执行机会，具体动作继续由 Agent Runtime、Todo Reminder 等现有领域承担。该能力必须适合本地桌面产品，不依赖外部调度服务，也不能因为任务在后台触发而扩大 Agent、Skill 或工具权限。

## 2. 目标

- 让用户能够创建、查看、修改、暂停、恢复、删除和立即运行全局 Scheduled Agent Task。
- 支持一次性与基础周期 schedule，并在应用重启、设备睡眠或短期离线后采用明确、保守的错过处理语义。
- 让每次计划触发形成独立、持久化、可幂等识别的 occurrence，并与实际 Agent session/run 建立稳定关联。
- 让每次 Scheduled Agent Task 在独立 session 中执行，避免持续污染创建任务时的聊天或自动继承历次执行 transcript。
- 通过自然对话提供任务管理能力，并为未来客户端保留稳定的查询、来源标记和 task/occurrence/session/run 关联边界。
- 让任务能够使用保存的指令和可选 Skill，同时继续服从正常 persona、记忆、工具、预算、取消和权限边界。
- 建立可复用的时间调度协议与 driver，让 Todo Reminder 接入统一唤醒和恢复路径，同时保持现有 Reminder 数据和用户可见行为兼容。
- 本期只交付后端调度能力，不实现任何前端入口、列表、详情、导航、分组或提示。

## 3. 核心概念与边界

### 3.1 Scheduled Task 是持久任务定义

- Scheduled Task 描述“需要做什么、何时尝试、使用什么执行配置”，不是某一次已经开始的 Agent run。
- Scheduled Task 是全局用户数据，不归属于创建它的会话；本期由 Agent 的自然对话工具查看和管理，未来客户端通过同一后端边界接入。
- 创建任务的会话只记录来源与用户意图，不成为后续运行内容的默认投递位置或执行上下文真相源。
- 一次性任务完成计划触发后不再产生新的 occurrence；周期任务在仍为 active 时继续计算未来触发点。

### 3.2 Occurrence 是一次计划触发事实

- 每个计划触发时间至多形成一个稳定 occurrence，用于记录原计划时间、实际处理时间和最终结果。
- occurrence 与 Scheduled Task、执行 session 和 Agent run 分别关联；它不因任务后续修改、暂停或删除而被改写成另一份历史。
- 跳过、过期、等待用户、失败、取消和成功都属于可查询结果，不能只保留成功运行。
- occurrence 只描述本次触发与执行状态，不拥有 Todo 卡片、IM 文案、Agent 回复内容或某个 Skill 的业务语义。

### 3.3 Agent run 与任务定义分离

- 每个需要 Agent 执行的 occurrence 创建独立 session，并在其中启动一个正常 Agent run。
- 新 session 使用任务保存的指令、可选 Skill、当前 persona 和正常的全局记忆能力，但不自动继承上一次 Scheduled run 的完整 conversation transcript。
- 后端稳定保存 task、occurrence、session 与 run 的关联和来源元数据，保证未来可以按任务查询历次运行，而不需要改造调度核心。
- 某次 run 在前端如何呈现、是否从任务历史打开成 Agent 对话、打开后是否允许继续对话，均不在本期决定。
- “周期回到同一现有会话继续处理”属于另一类短期 follow-up / monitor 能力，不与本期全局 Scheduled Task 混用。

## 4. 功能范围

### 4.1 Task 生命周期与统一入口

- 用户通过自然对话创建和管理 Scheduled Agent Task；本期不提供前端表单或管理入口。
- 创建时至少明确任务名称、执行指令和 schedule；可以选择一个当前可用的 Skill 作为任务工作流。
- 用户可以通过 Agent 查看任务详情、下次计划时间、当前状态和最近运行摘要。
- 用户可以修改任务名称、指令、Skill、schedule 和允许修改的执行配置。
- 用户可以暂停或恢复任务。暂停只阻止未来 occurrence，不取消或改写已经产生的 occurrence/run。
- 用户可以立即运行任务；手动运行形成正常 occurrence/run 并进入同一历史，而不是走旁路。
- 用户可以删除任务以停止未来执行。为了保留已发生事实，历史 occurrence 和已创建的 run session 默认不随任务定义一起不可恢复地删除。
- Agent 工具、内部调度器、后端查询接口和未来其他客户端必须调用同一应用服务，不各自维护任务状态或 schedule。
- 后端为未来 UI 保留稳定的任务查询、运行历史查询、来源标记和变更能力；本期不为这些能力接线任何前端代码。

### 4.2 Schedule 与时区

- 支持指定绝对日期时间的一次性任务。
- 支持基础固定间隔，以及每天、工作日、每周指定星期和每月指定日期等常用周期。
- 每个日历型 schedule 都必须具有明确 IANA 时区；用户没有指定时使用本次请求可验证的当前用户时区。
- 创建或修改后向用户展示规范化的时间、时区、周期和停止条件；关键信息不足时先澄清。
- 用户设备时区变化不会静默改变已有任务的时间语义；只有用户明确修改任务时区后才采用新规则。
- 月末、闰年、夏令时不存在或重复时刻采用稳定且可测试的语义，不因一次异常永久停用任务。
- 本期不要求用户直接输入或理解 cron、RRULE 等底层表达式。

### 4.3 指令、Skill 与运行身份

- 每个任务保存自包含、可重复执行的指令；指令应描述每次运行要做什么、什么结果值得报告，以及无法继续时如何向用户说明。
- 任务可以绑定一个可由 Agent 正常使用的 Skill。Skill 只提供工作流说明和已有公共能力，不因被定时调用而获得额外权限。
- 每个 occurrence 记录实际使用的 Skill 来源和内容身份。任务创建后同一逻辑 Skill 的合法更新可以用于后续 occurrence，但已经开始的 run 使用其启动时冻结的内容身份。
- Skill 缺失、来源冲突、内容不可确认或当前版本不再可用时，本次 occurrence 进入明确的待处理或失败状态，不回退到同名但来源不明的内容。
- 任务运行使用创建或最后修改时确认的执行配置；无法继续使用所选模型、Skill 或必要能力时明确暴露原因，不静默切换身份、Provider 或权限级别。

### 4.4 独立 session 与可查询关联

- 每个 occurrence 创建的新 session 明确标记其 Scheduled Task 来源、计划时间和运行身份。
- 后端可以按 task 查询历次 occurrence，并从 occurrence 定位实际 session/run、计划时间、开始时间、终态和失败或跳过原因。
- 原始创建会话不自动接收每次运行的完整 transcript；后续 Scheduled occurrence 也不继承前一次 run 的完整 transcript。
- 即使模型没有生成正文、run 在开始前被跳过或执行停在审批处，后端历史仍保留准确状态和原因。
- 本期不规定这些关联在现有会话列表或未来 Scheduled 工作区中的展示、分组、未读和交互方式。

### 4.5 错过、重启与非重叠执行

- 应用和 Bridge 正常运行时，任务在合理误差内产生 occurrence；不要求用户先发送消息。
- 应用重启后恢复所有 active 任务及未完成 occurrence，不把已经成功处理的计划点重新当作新任务。
- 本期采用固定的 `run_latest + 7 天` 错过策略，不开放任务级补跑配置：恢复时只考虑过去 7 天内最近一个错过的计划点，并至多补运行一次。
- 同一任务错过多个计划点时不逐个补跑；更早或超过 7 天的计划点按过期或合并事实留存可审计原因，然后继续未来 schedule。
- 任务从 paused 恢复时只从当前时间计算未来计划点，不补跑暂停期间的计划点。
- 同一 Scheduled Task 同时最多有一个非终态 Agent run。新的计划点到达而上一次仍未结束时，不并发启动第二个 run，并为本次 occurrence 记录稳定的跳过原因。
- 不同 Scheduled Task 仍服从 Agent Runtime 现有的跨 session 有界并发、优先级、容量和 shutdown 规则。
- Agent run 已经开始后发生失败或中断时，不自动重放整个 Agent 工作流，避免重复外部副作用；下一次周期仍可按未来计划正常运行。
- 只有在 Agent run 尚未提交、尚未产生副作用的基础设施阶段失败时，才允许有界恢复，并保持 occurrence 身份不变。

### 4.6 权限与待处理运行

- Scheduled Task 不继承创建会话中已经消费或失效的一次性批准。
- 本期不新增当前 turn、当前 session、持久规则、命令白名单或任务级永久授权。
- Scheduled run 调用无需额外授权的能力时可以正常完成；需要用户批准时进入现有审批流程，并在 occurrence/run 中保持可查询的等待状态。
- 用户后续通过既有审批能力完成批准、拒绝或取消时，继续使用同一 session/run/occurrence，不另建一次伪装成重试的新运行。本期不新增审批 UI。
- 等待审批的 run 仍算同任务的非终态运行；它结束前到达的后续 occurrence 按非重叠规则处理。
- 未来若增加持久无人值守授权，必须以独立需求定义资源、动作、作用域、内容身份、撤销和审计语义，不能把本需求的 schedule 当成授权载体。

### 4.7 现有能力迁移与兼容

- Todo Reminder 继续拥有 Todo/Reminder 生命周期、冻结事实、固定卡片或文本、目的地选择和 Agent 增强回应；Scheduled Task 内核不接管这些领域语义。
- Todo Reminder 通过 adapter/driver 接入统一调度协议；本期保留现有 Reminder 表、occurrence、投递结果和领域服务，不迁移既有数据所有权。
- 接线后现有 Reminder 数据、用户可见历史、到期生成、领取和恢复行为不得丢失、重复或退化。
- Reminder 更新、完成、取消与到期触发的现有并发失效语义继续成立。
- `BedtimeSource` 与 `IdleReflectionSource` 本期均保持现状；它们是否迁移到统一边界由后续需求决定。
- `RunScheduler` 继续拥有 Agent run 的 session 串行、跨 session 并发、优先级、抢占、取消和 drain；Scheduled Task 不复制第二套 Agent 执行器。

### 4.8 桌面生命周期

- 本期 Scheduled Task 只在本地桌面应用及 Bridge 可运行时执行，不承诺云端或远端持续运行。
- 应用完全退出时不运行任务，也不留下 detached daemon；重新启动后按错过策略恢复。
- active schedule 本身不构成长期阻止电脑休眠的理由。真正开始的 Agent run 继续按现有后台在线策略表达活跃任务需求。
- 系统休眠期间不尝试本地执行；唤醒或应用恢复后按错过语义产生至多一个适用补偿 occurrence。
- 设置中的后台在线政策、电源状态和用户主动睡眠边界继续由现有桌面电源治理能力负责。

## 5. 用户体验与信任边界

- 用户说“每天早上九点运行这个 Skill”时，Agent 必须调用持久化工具，并在创建成功后回显任务名称、Skill、绝对时间、时区和周期；不能只在聊天里口头答应。
- 用户说“暂停每周总结”时，目标不唯一应先列出候选；不能按相似名称静默暂停错误任务。
- 用户可以通过自然对话查询下一次何时运行、最近一次发生了什么，以及是否正在等待自己的批准。
- 一次运行失败不应让任务从查询结果中消失，也不能把失败伪装成正常完成；周期任务是否继续保持 active 与本次 run 结果分别表达。
- 每次 run 的模型回复和工具结果是该次 session 的事实，不反向成为任务 schedule、权限或 Skill 身份的真相源。
- 删除或暂停任务不撤销已经完成的外部副作用，也不篡改历史运行内容。

## 6. 不包含

- 任意 cron、原始 RRULE、节假日日历、复杂条件组合或完整工作流编排器。
- 基于 webhook、GitHub、IM、文件变化、位置、设备活动或其它外部事件的触发器。
- 绑定现有会话反复执行、短期 polling loop、部署 babysit 或由 Agent 动态决定下一次间隔的 monitor 模式。
- 跨运行自动继承完整 conversation transcript，或新建一套 Scheduled Task 专属长期记忆系统。
- 当前 turn、当前 session、持久规则、命令白名单、任务级 always-allow 或其它新的无人值守授权等级。
- 应用退出后的本地 daemon、操作系统任务计划程序、原生系统唤醒、云端 scheduler、跨设备执行或远端高可用。
- 用 Scheduled Task 替代现有 Todo 管理、Reminder 固定事实投递、IM 路由、Agent Runtime 或命令运行时。
- 迁移 `BedtimeSource`、`IdleReflectionSource`，或把所有条件型 EventSource 一次性重构成通用触发框架。
- 每任务 `run_latest` / `skip`、`max_lateness`、补跑预设或“全部补跑”等可配置策略；本期固定使用 `run_latest + 7 天`。
- 任何前端或桌面 UI 改动，包括左侧导航、Scheduled 独立工作区、任务列表/详情/表单、会话列表分组、未读提示、点击 run 后的页面形态，以及是否允许在 run 对话中继续交流。

## 7. 验收条件

- [ ] 用户可以通过自然对话创建一次性或基础周期 Scheduled Agent Task，并看到规范化时间、时区、指令和可选 Skill。
- [ ] 用户可以通过自然对话查看、修改、暂停、恢复、删除和立即运行任务；所有 Agent 工具共享同一后端状态，应用重启后保持一致。
- [ ] 每个计划触发至多形成一个稳定 occurrence；成功、失败、取消、跳过、过期和等待用户均有可查询历史，不因任务后续修改而丢失。
- [ ] 每个需要 Agent 执行的 occurrence 创建独立 session/run，并稳定关联 task、occurrence、session 和 run；下次周期运行不继承上次完整 transcript。
- [ ] 后端可以按 task 查询配置、下次计划时间和历次 occurrence，并定位每次实际 session/run 及其终态；原创建会话不会被历次 Scheduled run 自动污染。
- [ ] 任务绑定 Skill 时，每次 run 记录实际 Skill 来源和内容身份；已开始运行的 Skill 内容保持冻结，缺失、冲突或不可确认时失败关闭。
- [ ] 应用或 Bridge 恢复时固定执行 `run_latest + 7 天`：只补过去 7 天内最近一个错过的计划点且至多一次；更旧和重复错过有可审计原因，暂停期间不补跑。
- [ ] 同一任务已有非终态 run 时不会并发启动新的 run；被跳过的 occurrence 保留明确原因，不同任务仍按 Agent Runtime 的有界并发执行。
- [ ] 已开始的 Agent run 失败或取消后不会自动重放整个工作流；尚未提交且无副作用的基础设施失败可以在同一 occurrence 内有界恢复。
- [ ] Scheduled Task 不继承一次性批准，也不产生新的持久授权；需要批准的 run 保持可查询等待状态，并能通过既有审批能力继续、拒绝或取消。
- [ ] Todo Reminder 通过 adapter/driver 复用统一调度边界且不迁移现有数据所有权；Todo、Reminder、occurrence、投递历史、错过合并、并发失效和单目的地行为不丢失、不重复、不退化。
- [ ] Bedtime 与 IdleReflection 不在本期迁移，现有行为保持不变。
- [ ] Scheduled Task 不复制 Agent 执行器；交付后的运行继续具备现有 session 串行、跨 session 并发、优先级、抢占、取消和 drain 语义。
- [ ] 应用完全退出或设备休眠期间不声称任务已执行；恢复后按错过策略处理，active schedule 本身不会长期阻止系统休眠。
- [ ] 本期不修改任何前端导航、页面、组件、状态或样式；未来 UI 接入不要求重写调度核心，新增的聚合查询或事件适配应限制在后端接线层。
- [ ] 自动化测试覆盖 schedule 时间边界、DST、重启恢复、固定错过合并、occurrence 幂等、claim/失活恢复、非重叠执行、审批等待、Skill 身份和 Reminder adapter。
- [ ] 默认自动化测试不调用真实 LLM、外部付费 API 或外部 scheduler 服务，项目统一检查脚本全部通过。

## 8. 关键信息

- 背景 issue：[`docs/issues/050-global-scheduled-agent-job-boundary/README.md`](../../issues/050-global-scheduled-agent-job-boundary/README.md)
- 延期 UI issue：[`docs/issues/056-scheduled-task-workspace-information-architecture/README.md`](../../issues/056-scheduled-task-workspace-information-architecture/README.md)
- 主动轮与 RunScheduler：[`docs/requirements/014-engine-main-loop-and-bridge-push/`](../014-engine-main-loop-and-bridge-push/)
- 桌面后台在线政策：[`docs/requirements/057-desktop-background-awake-policy/`](../057-desktop-background-awake-policy/)
- Todo Reminder：[`docs/requirements/070-todo-management-and-reminders/`](../070-todo-management-and-reminders/)
- Agent Skill：[`docs/requirements/076-agent-skill-system/`](../076-agent-skill-system/)
- 工具执行治理：[`docs/requirements/082-tool-execution-governance/`](../082-tool-execution-governance/)
- 本地命令运行时：[`docs/requirements/085-governed-local-command-runtime/`](../085-governed-local-command-runtime/)
- 调研与选型证据：[`evidence-index.md`](./evidence-index.md)
- 本需求分支：`feature/099-global-scheduled-agent-jobs`

## 9. 变更记录

| 日期 | 变更内容 | 影响范围 |
|---|---|---|
| 2026-08-23 | 首次确认：建立全局 Scheduled Agent Task 与持久 schedule/occurrence 边界；每次触发使用独立 session/run 并由 Scheduled 聚合；复用现有 Agent Runtime；迁移 Reminder 时间机制与 Bedtime；持久无人值守授权、conversation-bound loop 和外部触发另行处理 | 新需求 |
| 2026-08-23 | 收缩本期范围：完全移除前端实现；只保留未来 UI 所需的后端查询与来源关联边界；补跑固定为 `run_latest + 7 天`，不做任务级策略配置；Reminder 以 adapter/driver 接入且不迁移数据；Bedtime 与 IdleReflection 迁移延期 | 目标、功能范围、非目标、验收条件 |
