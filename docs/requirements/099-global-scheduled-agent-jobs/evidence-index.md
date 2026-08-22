# 099 · 全局 Scheduled Agent Task 与持久调度边界 - 证据索引

## 用途与范围

- 档案归属：[`requirement.md`](./requirement.md)
- 本索引记录：issue 050 背景、agent-friend 现有调度与权限边界、scheduler 生态选型、主流 Agent 产品的 Scheduled Task 运行与管理实践，以及需求范围确认依据。
- 本索引不记录：未来技术方案的具体表结构、接口字段、迁移 SQL、实现 diff、真实 LLM 运行或未发生的桌面验收。
- 最近对账：2026-08-23，已与产品代码锚点 `a11e0d425afd035fa47e9daf2fc1fc9e3205d610`、相关需求文档和本次已确认范围对账。

## 当前综合判断

agent-friend 的核心缺口不是缺少一个“到点调用函数”的 timer，而是缺少由产品拥有的持久 Scheduled Task、occurrence、领取、恢复和 Agent run 关联边界。现有稳定 scheduler 库不能在不保留第二份产品状态或重复执行层的前提下覆盖该边界：APScheduler 3.x 的稳定模型仍把 schedule 与 job 合并，4.x 匹配的 `Task → Schedule → Job` 架构尚处于预发布；轻量库缺少持久恢复，任务队列与分布式工作流系统又会复制或替换现有 Agent Runtime。

需求因此保留本地单 owner、SQLite 与现有 Agent Runtime，建立共享 schedule/driver/coordinator 并继续复用成熟时间基础库。任务定义全局持久化，每次 occurrence 创建独立 session/run，并保存未来可聚合查询的稳定关联；绑定现有会话的重复 loop 留作独立模式。099 本期完全不实现 UI，Scheduled 工作区、Task/Run 层级、run 是否打开为可续聊对话等问题登记到 issue 056。错过策略固定为 `run_latest + 7 天`，周期最小间隔为 1 小时，不实现任务级策略系统；Scheduled session 默认不进入普通会话列表；Agent 提交后采用 at-most-once 崩溃恢复。Reminder 通过 adapter/driver 接入且保留既有数据所有权，Bedtime 与 IdleReflection 暂不迁移。Scheduled Task 不承载或扩张持久授权。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-23 | agent-friend 当前有哪些相互分离的调度边界？ | 代码 | commit `a11e0d4`; `agent/runtime/sources.py`; `agent/runtime/scheduler.py`; `agent/todos/scheduler.py`; `agent/todos/store.py`; `agent_bridge/reminders.py` | Bedtime/Idle 各自在线程等待；Reminder 独立持久化 schedule、occurrence、claim 和投递；RunScheduler 只处理已生成 run。缺口是跨领域的持久时间触发边界，不是缺少线程池。 | confirmed | active | 本表代码路径 |
| E-002 | 2026-08-23 | issue 050 与既有需求如何界定缺口？ | 文档 | `docs/issues/050-*`; requirements 014/057/070/076/082/085 | 070 有可靠 Reminder 机制但刻意未抽通用框架；085 只有 run-scoped one-shot 授权并排除定时/无人值守；057 不承诺 OS 唤醒。 | confirmed | active | [`../../issues/050-global-scheduled-agent-job-boundary/README.md`](../../issues/050-global-scheduled-agent-job-boundary/README.md) |
| E-003 | 2026-08-23 | 是否应直接采用 APScheduler 作为核心真相源？ | 外部资料 + 代码适配分析 | APScheduler 3.11.3 stable；4.0.0a6 pre-release；仓库 lock 间接依赖 3.11.3 | 3.x 提供持久 JobStore、misfire、coalesce、max instances，但 schedule/job 合一、JobStore 序列化且不支持多 scheduler 共享；4.x 才拆 Task/Schedule/Job 并重做 acquisition，但尚未正式稳定。直接采用会产生产品 task/occurrence 与 library JobStore 的双真相或要求自写 JobStore adapter。 | strongly-supported | active | [3.x guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)、[3.x FAQ](https://apscheduler.readthedocs.io/en/3.x/faq.html)、[4.x migration](https://apscheduler.readthedocs.io/en/master/migration.html)、[PyPI](https://pypi.org/project/APScheduler/) |
| E-004 | 2026-08-23 | 其它 scheduler/task-queue 是否更匹配本地桌面边界？ | 外部资料 + 适配分析 | schedule 1.2; Huey 3.3; Celery 5.6; Temporal Schedule | `schedule` 明确不提供持久化与并发；Huey 会引入 consumer/worker，动态 periodic registry 与结果仍需额外治理；Celery/Temporal 要求新的 broker/service/worker 拓扑。它们适合其它部署模型，但不能无重复地接入现有本地 Agent Runtime。 | strongly-supported | active | [schedule](https://schedule.readthedocs.io/en/stable/)、[Huey guide](https://huey.readthedocs.io/en/stable/guide.html)、[Celery periodic tasks](https://docs.celeryq.dev/en/main/userguide/periodic-tasks.html)、[Temporal Schedule](https://docs.temporal.io/schedule) |
| E-005 | 2026-08-23 | 持久 Scheduled Task 应复用原聊天还是每次创建独立 run？ | 外部产品资料 | OpenAI Scheduled tasks docs, fetched 2026-08-23 | Codex 区分 standalone task 与 in-chat task：standalone 每次从保存指令开始并在 Scheduled 聚合；只有需要既有上下文时才回到原 chat。这支持把全局持久任务与短期 conversation-bound follow-up 分开。 | strongly-supported | active | [OpenAI Scheduled tasks](https://developers.openai.com/codex/app/automations) |
| E-006 | 2026-08-23 | 本地与云端 Agent 自动化如何组织 run history？ | 外部产品资料 | Claude Desktop Scheduled Tasks / Routines / `/loop`, fetched 2026-08-23 | Claude Desktop 与 Routines 每次触发创建新 session，并在任务详情聚合运行历史；`/loop` 才复用当前 session。任务详情保留完成、失败、跳过与权限等待等运行事实。 | strongly-supported | active | [Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)、[Routines](https://code.claude.com/docs/en/routines)、[`/loop`](https://code.claude.com/docs/en/scheduled-tasks) |
| E-007 | 2026-08-23 | 跨次学习是否需要继承完整 transcript？ | 外部产品资料 | Cursor Automations / run history, fetched 2026-08-23 | Cursor 每次触发新的 Cloud Agent run，运行历史单独聚合；跨次学习通过显式 Memory 能力完成，而不是无限复用上一 run 的对话 transcript。 | directional | active | [Cursor Automations](https://cursor.com/changelog/03-05-26)、[run history](https://prod.cursor.com/docs/security-agents) |
| E-008 | 2026-08-23 | 需求范围中的初始关键边界是否已获用户确认？ | 人工评审 | 当前 feature-delivery 会话 | 用户确认：持久无人值守授权留作独立需求；Reminder 保持兼容；每个 occurrence 使用独立 session/run。原先同时包含 Scheduled UI 聚合与 Bedtime 迁移的范围已被 E-010 收缩。 | confirmed | retained | 当前需求讨论，无独立原始 artifact |
| E-009 | 2026-08-23 | 错过任务的保守默认应如何收敛？ | 外部产品资料 + 框架实践 | Claude Desktop Scheduled Tasks；APScheduler misfire/coalescing；Kubernetes CronJob deadline/concurrency policy，fetched 2026-08-23 | 主流实践通常同时限制补跑新鲜度、合并多次错过并阻止同任务并发；Claude Desktop 公开采用只补最近一次且限制在 7 天内。结合本地桌面离线模型，099 选择固定 `run_latest + 7 天`，不在首期暴露任务级参数。 | strongly-supported | active | [Claude Desktop Scheduled Tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)、[APScheduler guide](https://apscheduler.readthedocs.io/en/3.x/userguide.html)、[Kubernetes CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/) |
| E-010 | 2026-08-23 | 首期交付范围应如何避免膨胀？ | 人工评审 | 当前 feature-delivery 会话 | 用户明确要求 099 本期完全不做 UI，只设计后端边界；Task/Run 信息架构、run 对话形态与可续聊性延期。同步确认固定补跑策略、Reminder adapter 保持数据、Bedtime/Idle 不迁移的收缩方向。 | confirmed | active | 当前需求讨论；延期项见 [`../../issues/056-scheduled-task-workspace-information-architecture/README.md`](../../issues/056-scheduled-task-workspace-information-architecture/README.md) |
| E-011 | 2026-08-23 | 技术方案中的首期安全与投影边界是否确认？ | 人工评审 + 代码现状 | 当前 feature-delivery 会话；AgentRuntime、SessionManager、ReminderScheduler、SkillCatalogService | 用户确认：周期最小 1 小时；Scheduled session 默认不进入普通会话列表但保留完整 session；Agent 提交边界后采用 at-most-once 恢复。方案据此采用共享 coordinator + 领域 driver、独立 Scheduled store 和正常 AgentRuntime 执行路径。 | confirmed | active | [`design.md`](./design.md) |
| E-012 | 2026-08-23 | 099 的源码、兼容性与 frozen runtime 路径是否形成可复查验证？ | 自动化验证 + frozen capability smoke | backend check；build `20260822T193203Z-a11e0d425afd-a433a8b9`；Guide baseline `9ef875b` / snapshot `1d4051…` | 最终 backend check 为 2113 passed、4 skipped，lint/typecheck 全绿；Windows staged doctor `ok: true`，新增 `capability:scheduled_tasks` 在 frozen exe 内实际覆盖工具创建、SQLite 到期生成、claim/submitted、启动恢复为 interrupted 与 history 投影；未调用 LLM 或外部 scheduler。 | confirmed | active | [`progress.md`](./progress.md)；本机 package workspace（不可分发 local-test） |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| Scheduled 桌面工作区如何组织？ | 与对话同级 / 其它入口；`Task → Run` / 其它层级；run 只读 / 可续聊 | 作为 issue 056 的独立需求调研，不在 099 设计中展开 | 不阻塞 099；阻塞未来 UI 需求 |
| APScheduler 4.x 是否值得未来重评？ | 正式版可以删除自有机制 / 即使正式版仍需产品 occurrence 内核 | 等 4.x final、SQLite data store、迁移与打包成熟后，用“能否删除而非包裹现有内核”重新评估 | 不阻塞当前需求 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 不把现成 scheduler/task-queue 作为 099 的领域真相源；提取现有可靠机制并复用时间基础库 | E-001, E-002, E-003, E-004 | 当前 | APScheduler 4.x 或其它稳定嵌入式方案能原生承载产品 occurrence、事务、Agent run adapter 和迁移，并实际删除更多代码时 |
| 全局 Scheduled Task 每个 occurrence 使用独立 session/run；本期只保存可查询关联，不实现 Scheduled UI 聚合 | E-005, E-006, E-007, E-008, E-010 | 当前 | 用户明确需要持续复用同一 conversation context，且独立 follow-up/monitor 模式无法满足时 |
| 099 不实现新的持久无人值守授权 | E-002, E-005, E-006, E-008 | 当前 | 用户启动平级授权需求并确认资源、动作、scope、内容身份与撤销模型时 |
| Reminder 以 adapter/driver 接入共享调度协议，保留既有数据所有权与固定事实投递语义 | E-001, E-002, E-008, E-010 | 当前 | 若设计阶段证明 adapter 无法避免双调度或一致性问题，必须回到需求阶段重新确认迁移边界 |
| 错过策略固定为 `run_latest + 7 天`，不实现任务级配置 | E-006, E-009, E-010 | 当前 | 用户另行提出不同任务确有不同新鲜度需求，并愿意承担额外产品与测试复杂度时 |
| 099 不实现前端；Scheduled 工作区与 Run 交互延期到 issue 056 | E-005, E-006, E-010 | 当前 | issue 056 转为独立需求并完成产品边界确认时 |
| Bedtime 与 IdleReflection 本期均不迁移 | E-001, E-002, E-010 | 当前 | 后续需求明确要统一既有主动/条件触发能力时 |
| 周期最小间隔为 1 小时；一次性任务仍可分钟级 | E-006, E-009, E-011 | 当前 | 真实用户场景证明分钟级周期的收益超过成本与误配置风险时 |
| Scheduled session 保持完整 session，但普通 list/search 默认不投影 | E-005, E-006, E-010, E-011 | 当前 | issue 056 确认新的桌面信息架构与 run 打开方式时 |
| occurrence 进入 Agent 提交边界后不自动重放，恢复时标记 interrupted | E-001, E-009, E-011 | 当前 | 引入可证明副作用幂等的工作流协议或用户明确选择重试策略时 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001 | commit `a11e0d425afd035fa47e9daf2fc1fc9e3205d610` 中的上述代码路径 | Git 历史，无额外复制 | commit 锚定 | 仓库内公开源码 |
| E-002 | issue 050 与 requirements 014/057/070/076/082/085 | Git 历史，无额外复制 | commit 锚定 | 仓库内项目文档 |
| E-003 | APScheduler 官方文档、PyPI 与仓库 `uv.lock` | URL + Git 历史 | 访问日期与版本锚定；未保存网页副本 | 公开资料 |
| E-004 | schedule、Huey、Celery、Temporal 官方文档 | URL | 访问日期锚定；未保存网页副本 | 公开资料 |
| E-005 | OpenAI 官方 Scheduled tasks 文档 | URL | 访问日期锚定；未保存网页副本 | 公开资料 |
| E-006 | Claude Code 官方 Scheduled Tasks / Routines 文档 | URL | 访问日期锚定；未保存网页副本 | 公开资料 |
| E-007 | Cursor 官方产品与 Security Agents 文档 | URL | 访问日期锚定；未保存网页副本 | 公开资料 |
| E-008 | 当前 feature-delivery 会话中的用户确认 | 未单独归档 | 由本索引记录结论；无 verbatim transcript | 当前会话可访问，不含凭据或用户数据 |
| E-009 | Claude Desktop、APScheduler 与 Kubernetes 官方文档 | URL | 访问日期锚定；未保存网页副本 | 公开资料 |
| E-010 | 当前 feature-delivery 会话中的用户范围确认 | issue 056 仅归档延期问题，不复制完整会话 | 由本索引记录结论；无 verbatim transcript | 当前会话可访问，不含凭据或用户数据 |
| E-011 | 当前 feature-delivery 技术方案确认与对应代码锚点 | `design.md` | 方案文档与确认范围对账；无 verbatim transcript | 仓库文档与当前会话可访问 |
| E-012 | backend check 与 package-sensitive local-test runtime | `progress.md` 记录命令结果和 build id；详细 staging 位于项目外本机 package workspace | 命令、build id、Guide commit/snapshot 与 doctor capability 锚定 | 本机临时验证产物，不含产品用户数据；不可分发 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-23 | issue 050、requirements 014/057/070/076/082/085、当前 scheduler/Reminder/AgentRuntime 代码、scheduler 生态与主流 Agent 产品资料、已确认需求边界 | 已修订；099 采用独立 session/run 与稳定后端关联，但完全移除 UI；固定 `run_latest + 7 天`；Reminder 走 adapter/driver 且不迁移数据；Bedtime/Idle 不迁移；UI 未决项登记到 issue 056 |
| 2026-08-23 | 已确认 requirement、当前 AgentRuntime/Session/Skill/Reminder 代码边界与 `design.md` | 一致；新增 1 小时周期下限、普通会话投影过滤和 at-most-once 提交恢复；设计未扩张到 UI、持久授权或既有主动源迁移 |
| 2026-08-23 | 最终实现 diff、定向回归、backend check、Guide package-baseline 与 Windows frozen doctor | 实现与确认范围一致；frontend、Bedtime、IdleReflection 未改；新增工具/REST/独立 store/session/run/审批投影与 Reminder driver 均有确定性覆盖；公开 Guide 后续需补充新用户能力，本次只读不写 |
