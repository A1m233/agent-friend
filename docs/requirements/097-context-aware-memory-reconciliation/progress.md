# 097 · 上下文辅助的 Semantic 纠错 · 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 提取并复用 compaction checkpoint resolution helper
- [x] 增加 Semantic update context 契约与 Agent PostTurn 投影
- [x] 实现完整轮容量保护与双阶段 Extractor
- [x] 扩展 extraction attempt 诊断与失败回退
- [x] 补齐 Agent、Memory、086 与 098 确定性回归测试
- [x] 运行受影响测试、项目全量门禁与必要的冻结 runtime 验证
- [x] 在单独授权后执行真实 LLM 观察型验收

## AC 验证

- [x] AC-1 无 compaction 的近期完整轮投影
- [x] AC-2 compaction 后连续 raw tail
- [x] AC-3 active lineage 与重启一致性
- [x] AC-4 异步输入冻结
- [x] AC-5 完整轮容量保护
- [x] AC-6 用户纠错 Semantic reconciliation
- [x] AC-7 助手自我纠错与歧义保护
- [x] AC-8 Semantic-only 隔离
- [x] AC-9 target-only provenance
- [x] AC-10 legacy no-context fallback
- [x] AC-11 诊断可解释
- [x] AC-12 机制测试与 LLM 观察样本
- [x] AC-13 086 / 098 / Core / Episodic / silent 回归

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-22 | 用户授权开始实现；按已确认 design 冻结 Agent 范围投影、Memory 双阶段抽取与 target-only provenance 边界 | 默认使用 fake/cassette，不调用真实外部 LLM；当前变更按 frozen-runtime rule 先分类，若触发 package-sensitive 门禁再运行标准 runtime 构建验证 |
| 2026-08-23 | 完成共享 checkpoint resolution、PostTurn raw-tail 完整轮投影、不可变 Semantic update context 与两条 observe 路径接线 | Conversation 继续维护既有 checkpoint diagnostics；Memory 不读取 Session，不新增数据库或队列持久化 |
| 2026-08-23 | 完成 target-only base extraction、Semantic-only reconciliation、完整轮动态容量保护、严格 parser、unknown-ref 预检和失败回退 | 无上下文仍只调用一次；有上下文时 Core/Episodic 固定来自 base，Reconciler 仍只使用目标 provenance |
| 2026-08-23 | 定向与模块回归 | 定向 99 passed / 1 skipped；Agent、Memory、memory_eval 与 bridge memory 路径 1145 passed / 4 skipped；mypy 547 个源码文件通过 |
| 2026-08-23 | backend 全量门禁 | lint、mypy 与 pytest 通过；2085 passed / 4 skipped；仅既有 duplicate ZIP warning 与 bilibili atexit warning，不影响退出码 |
| 2026-08-23 | package-sensitive frozen runtime 验证 | installer-contract 判定新增 reconciliation prompt 为 package-sensitive；绑定 immutable Guide r5 (`9ef875b…`) 构建 local-test runtime，PyInstaller、doctor、`resource:memory_prompt` 和全部 capability smoke 通过；未安装或发布制品 |
| 2026-08-23 | 自动化实现完成，等待真实 LLM 观察型验收授权 | AC-6 / AC-7 的机制由 fake 固定，纠错质量与 AC-12 的 LLM 样本尚未真实调用；总体状态保持 IN_PROGRESS |
| 2026-08-23 | 执行获授权的真实 LLM 观察型验收 | DeepSeek V4 Flash、temperature 0；5 个通用场景共 10 次请求，用户纠错、助手自我纠错、多候选歧义保护、助手不得覆盖用户事实、上下文不得独立写入均符合预期；两阶段均无解析失败或 fallback |
| 2026-08-23 | 固化高价值 memory_eval 回归 case | 仅 `semantic-context-assistant-correction` opt-in prior complete turn；Judge 同时要求正确 Semantic anchors 命中且错误旧指南 anchor 不得召回；不放入 agent_eval，不扩张普通数据集默认投影 |
| 2026-08-23 | case 固化后的最终 backend 门禁 | lint、mypy 与 pytest 通过；2088 passed / 4 skipped；installer-contract 的 package input SHA-256 仍为 `93139d…`，与已通过的 frozen candidate 完全一致，无需重复构建 |
| 2026-08-23 | 全部任务与 AC 已具备证据，等待用户验收确认 | 按 feature-delivery 约束，总体状态在用户明确确认前保持 IN_PROGRESS |
| 2026-08-23 | 用户确认验收并要求合入 `main`、推送远端 | 总体状态更新为 COMPLETED，进入受治理的 Guide 复核与 Git 合入流程 |
