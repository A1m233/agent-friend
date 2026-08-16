# 050 · 定时能力缺少解耦的全局 Scheduled Agent Job 边界

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-08-16
- **关联需求**：014-engine-main-loop-and-bridge-push、070-todo-management-and-reminders、085-governed-local-command-runtime

## 现象

项目已经具备多种与时间或调度有关的实现，但职责分散在不同领域：

- `BedtimeSource` 和 `IdleReflectionSource` 在进程内自行等待并提交系统事件；
- `ReminderScheduler` 持久化 Todo Reminder 的 recurrence、occurrence、claim 和恢复；
- `RunScheduler` 只负责 Agent run 的队列、优先级、并发、取消和 drain。

当未来需要“每天运行一个 Skill”“定时执行个性化日常流程”等通用主动任务时，目前没有独立于 Todo、Reminder 文案和投递策略的全局 Scheduled Agent Job 边界。直接复用 Reminder 会把自动化任务错误耦合到 Todo 领域；继续新增独立 EventSource 又会复制等待、恢复、幂等和重试逻辑。

## 根因初判

现有能力按需求逐步形成：014 先提供主动事件来源和 Agent run 调度，070 再为用户提醒实现可靠持久化调度。070 的设计明确避免提前引入通用任务框架，因此 recurrence、occurrence 和 claim 目前仍由 Todo Reminder 领域拥有；085 又明确排除了定时任务和无人值守自动化。

缺口不是“没有定时线程”，而是尚未把稳定的时间触发机制与具体领域载荷、投递方式和执行授权解耦。

## 影响与判断

- 新增定时 Skill、周期 Agent 工作流或其它主动任务时，容易复制 scheduler，或把非提醒任务塞进 Todo Reminder。
- 重启恢复、错过合并、幂等领取、租约和有界重试难以形成单一公共契约。
- 通用命令运行时目前只有现场“仅本次”授权；即使能准时创建 Agent run，也不能据此自动获得无人值守命令权限。
- 当前 Todo Reminder 和既有主动关怀仍可正常工作，因此这是架构扩展缺口，不是现有用户数据正确性故障，暂不在需求 085 内修复。

## 可选修复方向

- 建立持久化的 `ScheduledAgentJob` / `JobOccurrence` 领域，只负责结构化 schedule、到期领取、恢复、幂等和执行状态，不拥有 Todo、Reminder 卡片或目的地策略。
- 复用 070 已验证的 recurrence 计算、condition wake、occurrence 唯一键、claim lease、错过合并和有界重试语义；先提取稳定机制，不预先建设任意 cron 或完整工作流平台。
- 到期后统一提交结构化系统事件到 `AgentRuntime`，继续由现有 `RunScheduler` 处理同 session 串行、优先级、取消和 drain。
- 将 Job payload、目标 session、Skill 选择和投递策略设计为上层适配器，避免调度内核依赖某个业务。
- 无人值守命令执行另行设计受限的持久授权，至少绑定固定 Skill 内容身份、入口、目录和能力范围；不得把一次性命令批准直接升级为任意后台 shell 权限。
