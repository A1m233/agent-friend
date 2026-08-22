# 098 · 当前可见对话的自动召回抑制 · 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 增加 automatic recall scope、active order provider 与 scope decision contracts
- [x] 实现 provenance 规范化、集合判定与 per-source 过滤/补位
- [x] 实现 SessionStore active lineage provider 并完成生产装配
- [x] 在 Memory 门面和 Conversation 薄接线 scope，保持 Core 唯一豁免
- [x] 扩展 trace，保留 boundary 与 fusion 两阶段决定
- [x] 补齐 memory、agent、agent_bridge 确定性回归测试
- [x] 运行受影响测试与项目全量门禁

## AC 验证

- [x] AC-1 无压缩边界
- [x] AC-2 首次截止边界
- [x] AC-3 边界推进
- [x] AC-4 新 compaction 后续生效
- [x] AC-5 Core 永久召回
- [x] AC-6 其他会话不受影响
- [x] AC-7 跨数据源一致
- [x] AC-8 来源顺序未知时不绕过
- [x] AC-9 生命周期边界
- [x] AC-10 显式历史查询例外
- [x] AC-11 trace 可解释
- [x] AC-12 既有行为不退化

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-22 | 开始实现；冻结纯 memory 范围与不触碰 `agent/context/**` 的边界 | 默认使用 fake/store fixture，不调用真实外部 LLM |
| 2026-08-22 | 完成 scope contracts、provenance 集合过滤、渐进补位、active lineage provider、Conversation 薄接线与 trace | 未修改 `agent/context/**`、memory schema、compaction schema、Extractor 或显式历史工具协议 |
| 2026-08-22 | 定向回归 | 268 passed，2 skipped；新增 `test_search_service.py` 21 项通过；edit/resend/compaction 后续生效 22 项通过 |
| 2026-08-22 | backend 全量门禁 | lint、mypy 与 pytest 通过；2072 passed，4 skipped（第三方 bilibili atexit warning 不影响退出码） |
| 2026-08-22 | frontend 全量门禁 | ESLint、颜色/来源/token guards、TypeScript、604 tests 与 production build 通过；仅既有 CSS/chunk size warning |
| 2026-08-22 | rust 全量门禁 | cargo fmt、221 tests（1 ignored）与 release check 通过；仅既有 dead-code warning |
| 2026-08-22 | 实现与自动化验收完成 | 用户确认验收并要求合入 `main`、推送远端；总体状态更新为 `COMPLETED` |
