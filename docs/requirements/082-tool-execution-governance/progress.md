# 082 · 工具执行治理 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 诊断长会话中的 Todo 多次调用与延迟来源。
- [x] 明确原生业务批量、运行时并行、程序化编排和敏感写入边界。
- [x] 确认 Todo 使用同类操作的统一多项形态，不增加独立 batch 模式。
- [x] 确认工具结果默认 recoverable、必要时显式 pinned。
- [x] 完成需求文档确认。
- [x] 分析现有工具清单、调用方与兼容边界。
- [x] 形成并确认技术方案。
- [x] 获得用户实现授权。
- [x] 实施 Todo 原生批量、结果保留默认值和必要测试。
- [x] 完成自动化验收与项目检查。

## AC 验证

- [x] 工具执行分类与内置工具盘点可审查。
- [x] Todo 同类多目标操作可由一次调用完成。
- [x] Todo 批量结果具有逐项 revision、错误和稳定顺序语义。
- [x] Todo 批量结果保持紧凑。
- [x] 默认 recoverable 与显式 pinned 例外正确。
- [x] 无恢复入口时的自动 pin 兜底不变。
- [x] 需求 075 的既有恢复机制与保护窗口不回归。
- [x] 敏感写入未被无条件批量化。
- [x] 项目完整检查通过且不依赖真实 LLM。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-13 | 完成长会话证据定位、工具治理原则讨论并确认需求范围。 | 需求 082 已确认；当前分支 `feature/082-tool-execution-governance`；尚未进入技术方案或实现。 |
| 2026-08-13 | 完成内置工具分类、Todo 批量边界和默认 recoverable 方案确认。 | 仅 `complete/cancel/reopen` 使用原生 items；`update/create/get` 与 Reminder 操作保持单项；尚未获得实现授权。 |
| 2026-08-13 | 用户明确授权开始实现。 | 按已确认 design 落地；不调用真实 LLM 或外部收费 API。 |
| 2026-08-13 | 完成 Todo 状态迁移原生批量、默认 recoverable、显式 pinned 例外和安全只读并行分类。 | 10 项完成可由一次 invoke 处理；批量结果支持有序逐项 revision/error 和部分成功；旧事件兼容及 075 恢复机制未改。 |
| 2026-08-13 | 完成局部回归与项目三段统一门禁。 | 局部 73 passed；backend lint/mypy 通过，1673 passed / 4 skipped；frontend 91 files / 525 tests 与 production build 通过；Rust 183 + 6 passed / 1 ignored，release check 通过；未调用真实 LLM。 |
| 2026-08-13 | 用户明确要求合入主干并推送，确认本需求验收完成。 | 需求实现与 AC 已完成；进入知识维护检查、提交与交付阶段。 |
| 2026-08-13 | 同步已合入需求 081 的最新 `origin/main`，知识维护检查结论为无需更新。 | 公开 guide 与 builtin 均精确绑定 `b2de939`；082 不改变用户入口、设置、凭据、数据或可见错误语义。 |
| 2026-08-13 | 完成主干同步后的完整项目门禁。 | backend lint/mypy 通过，1681 passed / 4 skipped；frontend 91 files / 525 tests 与 production build 通过；Rust 188 + 6 passed / 1 ignored，release check 通过。 |
