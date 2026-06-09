# 009 · 引擎层上下文管理 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->

IN_PROGRESS（M1 ~ M3 代码 + 单测全部落地并合入 main；机制级 AC 由测试覆盖；涉及真实 LLM 的观察型 AC 待按 `llm-api-confirm` 授权后验收）

## 里程碑与任务

### M1 · usage 透出 + 窗口查询 + context 包骨架 + 三入口收敛
- [x] `llm_providers` 透出真实 token `usage`（流式 + 非流式）+ context window 查询（三层兜底）
- [x] `agent/context/` 包骨架 + `budget.py`（`BudgetSnapshot` / 预留比例常量）
- [x] `build_messages` 新签名（可选 `new_user_input` / `trailing_system` + 单个 `runtime: RuntimeContext`）
- [x] 三处 LLM 上下文入口收敛到 `ContextManager`（含工具续轮、兜底收尾）
- [x] `NaiveContextManager` 跑通入口覆盖与度量可观测

### M2 · FIFO 兜底 + 工厂化装配
- [x] `agent/context/fifo.py`：`FifoContextManager`（按 token 预算裁剪 + 保护最近 N 轮下限）
- [x] `context_manager_factory` per-session 装配（默认可配 `Naive` / `Fifo` / `Summarizing`）
- [x] 单测：`test_context_fifo.py` / `test_context_budget.py` / `test_context_assembly.py` / `test_context_unified_entry.py`

### M3 · 摘要压缩 + compaction 事件 + 折叠 + circuit breaker
- [x] `agent/context/summarizing.py`：`SummarizingContextManager`（9 段式摘要 prompt + circuit breaker 退化 FIFO）
- [x] `compaction` 事件（002 事件流向后兼容新增）+ `Session.latest_compaction()` + 折叠投影
- [x] 默认策略切 `Summarizing`（`default_context_manager`）
- [x] 单测：`test_context_summarizing.py`（fake LLM 验机制：触发 / 折叠 / 跳闸退化 / 持久化复用）

## 验收小结

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1.2 | 阈值随 model 变 | ✅ 离线 | 窗口动态推导，无写死阈值（budget 单测） |
| AC-1.3 | 度量可观测 | ✅ 离线 | 调试入口展示 token 估算 / 有效窗口 / 阈值 |
| AC-2.1 | 长会话不抛错 | ✅ 离线 | FIFO 裁剪单测 |
| AC-2.2 | 保留近期上下文 | ✅ 离线 | FIFO 保护最近 N 轮单测 |
| AC-2.3 | 工具循环续轮不爆窗 | ✅ 离线 | 统一入口单测覆盖续轮 / 兜底 |
| AC-3.1 | 触发压缩腾窗 | ✅ 机制（fake LLM） | summarizing 单测验触发 + token 下降 |
| AC-3.3 | 跳闸退化 | ✅ 机制（fake LLM） | circuit breaker 退化 FIFO 单测 |
| AC-3.4 | 压缩可观测 | ✅ 离线 | 调试入口展示压缩前后 / 是否跳闸 |
| AC-3.5 | 摘要持久化复用 | ✅ 机制 | `compaction` 落盘 + `--resume` 复用单测；原始事件不动 |
| AC-0.1 | 不破坏既有接口 | ✅ 离线 | 全套 pytest 零回归 |
| AC-1.1 | usage 透出（真实调用） | ⏳ 待真 LLM | 需一次真实 LLM 调用观察 usage（`llm-api-confirm`） |
| AC-3.2 | 关键信息不漂移（人工观察） | ⏳ 待真 LLM | 需真实长会话压缩后人工观察 |

## 实施前置条件

- [x] requirement.md CONFIRMED
- [x] design.md CONFIRMED
- [x] 用户授权进入 Phase 3 实施

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-09 | M1 ~ M3 代码 + 单测全部落地并合入 main | 分支 feature/009-engine-context-management；lint / mypy / pytest 全绿 |
| 2026-06-09 | 补建本 progress.md | 此前缺失；据 design §11 里程碑与代码现状回填 |
