# 099 · 全局 Scheduled Agent Task 与持久调度边界 - 进度

## 总体状态

COMPLETED — 用户已验收本期后端范围

## 任务列表

- [x] 提取共享 schedule 类型、时间计算、driver 协议与 coordinator，并保持 Todo recurrence 兼容。
- [x] 将 Reminder 私有 scheduler 改为共享 coordinator 下的 driver，保持现有存储与投递语义。
- [x] 实现 Scheduled Task 模型、SQLite store、service、固定 `run_latest + 7 天` 与 occurrence 状态机。
- [x] 实现独立 session 来源、普通会话投影过滤、Skill 冻结、AgentRuntime 编排和 run tracker。
- [x] 实现 `scheduled_task_manage` Agent 工具、严格 schema、结果和错误契约。
- [x] 实现 Scheduled Task REST、Bridge 装配、设置与 lifespan 接线。
- [x] 补齐共享调度、领域、工具、REST、运行编排、Reminder 回归与生命周期测试。
- [x] 运行局部测试、backend check，并完成 `package-sensitive` frozen runtime 验证。

## AC 验证

- [x] 自然对话工具可创建一次性或基础周期任务，并返回规范化 schedule、时区、指令和可选 Skill。
- [x] 自然对话工具可查看、修改、暂停、恢复、删除和立即运行任务，重启后状态一致。
- [x] occurrence 幂等持久化成功、失败、取消、跳过、过期和等待事实。
- [x] 每个可执行 occurrence 创建独立 session/run，并稳定关联 task/occurrence/session/run。
- [x] 后端可按 task 查询配置、下次时间、历史与 session/run 终态。
- [x] Skill source/version 按 occurrence 冻结；不可用时不调用模型。
- [x] 恢复固定执行 `run_latest + 7 天`，暂停期间不补跑。
- [x] 同一 task 非终态 run 不并发；AgentRuntime 继续控制跨 task 并发。
- [x] Agent 提交后不自动重放，pre-submit claim 可安全恢复。
- [x] Scheduled Task 不继承或新增持久授权，审批事实沿用既有能力。
- [x] Reminder 复用共享 coordinator 且不迁移数据、不退化行为。
- [x] Bedtime/IdleReflection 保持现状。
- [x] 普通会话列表/搜索默认不投影 Scheduled session；无任何前端代码改动。
- [x] 自动化测试不调用真实 LLM、付费 API 或外部 scheduler。
- [x] 项目统一检查通过。
- [x] 用户明确确认验收完成。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-23 | 进入实现阶段，建立任务与 AC 跟踪 | 用户已明确授权“开始实现”；当前仅有需求文档与 issue 056 变更 |
| 2026-08-23 | 完成共享 coordinator、Reminder driver、Scheduled Task 领域、独立 session/run、Skill 冻结、Agent Tool、REST、审批投影与 Bridge 生命周期接线 | `frontend/` 零变更；Bedtime/IdleReflection 零变更 |
| 2026-08-23 | 完成定向回归与项目 backend check | 最终结果：2113 passed、4 skipped；lint/typecheck 全绿；无真实 LLM/付费 API |
| 2026-08-23 | 完成 Windows `package-sensitive` frozen runtime 验证 | build `20260822T193203Z-a11e0d425afd-a433a8b9`；staged doctor `ok: true`；`capability:scheduled_tasks` 真实覆盖工具、SQLite 到期生成、claim/submitted、interrupted 恢复与 history |
| 2026-08-23 | 只读完成 Guide package-baseline | Guide `9ef875b` / immutable `agent-friend-guide-r5` / snapshot `1d4051…`；当前 builtin 匹配；新能力后续需更新公开 product overview、using、data/privacy 与 troubleshooting，本次未修改或发布 Guide |
| 2026-08-23 | 用户确认本期后端范围验收通过 | 授权继续合入主干并推送远端；进入受管 Guide 与 Git 组合交付流程 |
| 2026-08-23 | 完成公开 Guide 更新与不可变发布 | Guide commit `cb4c253`；release `agent-friend-guide-r6`；snapshot `82c0ea5c…`；兼容范围 `[0.2.1, 0.3.0)` |

## 剩余收口

- [x] 用户确认并完成关闭顺序的方案文档校正：coordinator 停止后先 drain AgentRuntime，再停止 run tracker，以保留清洁关闭产生的真实终态。
- [x] 用户完成本期后端范围验收。
- [x] 合入流程中已按受管 Guide 工作流更新公开知识并发布 immutable `agent-friend-guide-r6`。
