# Semantic 多对多变更图 - 进度

## 总体状态
COMPLETED

## 任务列表
- [x] 定义 Semantic change v2 contracts，并兼容解析 v1 `semantic_ops`
- [x] 更新 Extractor prompt、attempt 与结果诊断字段
- [x] 实现 schema v6、旧数据迁移和多对多历史查询
- [x] 实现 Reconciler 的整批校验与原子变更应用
- [x] 增加 owner-gated Semantic change 诊断接口
- [x] 增加 memory_eval 通用契约样本与确定性验证
- [x] 补齐 parser、迁移、事务、查询和路由测试
- [x] 运行受影响测试与项目全量检查
- [x] 运行 Semantic change 切片的真实 LLM 评测并人工复核变更分组

## AC 验证
- [x] AC-1：统一模型覆盖新增、撤回与修正
- [x] AC-2：多旧到一新原子生效
- [x] AC-3：一旧到多新原子生效且保留分组
- [x] AC-4：多对多分组准确，独立变化不混合
- [x] AC-5：只撤回不生成新事实且不触发删除
- [x] AC-6：新旧成员理由必填，缺失时整批拒绝
- [x] AC-7：无效、越权或 stale 引用导致整批回滚
- [x] AC-8：v5 supersede 历史兼容迁移
- [x] AC-9：诊断可追溯变更成员、理由、来源和结果
- [x] AC-10：当前召回排除 retired，历史查询保留关系
- [x] AC-11：Core Profile 行为不受影响且不双写
- [x] AC-12：确定性测试与真实 LLM 评测覆盖约定场景

## 实现日志
| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-16 | 进入实现阶段，建立任务与 AC 跟踪 | 用户已明确授权开始实现；真实 LLM 评测仍需执行前单独告知与授权 |
| 2026-08-16 | 完成 v2 contracts、prompt、schema v6、迁移、原子 Reconciler、诊断 API 与 memory_eval 切片 | `memory + agent_bridge/tests/test_routes_memory.py`：112 passed；`memory_eval`：51 passed, 2 skipped |
| 2026-08-16 | 完成仓库级确定性门禁 | backend：lint/typecheck 通过，1742 passed / 4 skipped；frontend：93 files / 532 tests 通过并完成 production build；rust：202 tests 通过、1 ignored，release check 通过 |
| 2026-08-16 | 完成 Semantic change 首次真实 LLM 验证 | `deepseek/deepseek-v4-flash`，4 cases / 8 calls；自动 judge 4/4、Macro 1.0。人工复核确认：纯撤回后 active Semantic 为空；多对一在同一 change 撤回 2 条并引入 1 条；一对多在同一 change 撤回 1 条并引入 2 条；所有成员理由非空、contract version 均为 v2。baseline：`memory_eval/baselines/2026-08-16T15-28-03-9c61587.json`。结束时 `bilibili_api` 仅产生 event-loop atexit warning，发生在报告与 baseline 写入后，不影响 8 次调用、数据库提交或评测结果 |
| 2026-08-16 | 用户确认验收完成 | 全部任务、AC 与项目门禁满足，进入提交、合入与本地测试包流程 |
