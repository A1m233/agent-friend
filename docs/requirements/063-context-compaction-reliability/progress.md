# 063 · 上下文压缩可靠性 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 扩展 provider 上下文超限错误类型与精确映射
- [x] 建立统一预算估算、tool schema 成本与 usage 校准
- [x] 建立完整 turn 选择与 tool result 模型视图安全截断
- [x] 重构 FIFO 与摘要流程，补齐摘要输入预算和输出后二次验算
- [x] 增强 compaction checkpoint 并实现进程级恢复验证
- [x] 统一 Conversation 主请求提交与 provider 超窗一次性恢复
- [x] 补齐自动化测试并通过项目统一门禁
- [x] 完成真实 LLM 观察型验收（需逐次授权）
- [x] 用户验收确认

## 实现日志

| 日期 | 完成内容 | 备注 |
|---|---|---|
| 2026-07-25 | 用户确认技术方案并明确授权进入实现阶段 | design.md 已标记 CONFIRMED |
| 2026-07-25 | 完成预算校准、turn 选择、tool result 投影、安全摘要、checkpoint v2 与 provider 超窗恢复 | 针对性自动化测试已通过，待全量门禁 |
| 2026-07-25 | 通过统一门禁 | Ruff、mypy、1016 项 Python 测试、138 项 Rust 测试与 release check 通过；前端依赖未就绪，脚本按规则跳过前端步骤，本需求无前端改动 |
| 2026-07-25 | 真实 LLM 首轮暴露压缩后水位过高 | 首压成功但真实 usage 校准后，重启续聊立即产生第二个 checkpoint；未误报通过 |
| 2026-07-25 | 增加压缩后 70% 目标水位并通过复验 | 完整门禁更新为 1017 passed / 2 skipped；真实 DeepSeek smoke 固定 1 次摘要 + 3 次对话调用，checkpoint 重启复用通过 |
| 2026-07-25 | 用户确认验收完成 | 需求状态更新为 COMPLETED，进入提交与合入流程 |

## 验证记录

| 验证范围 | 结果 |
|---|---|
| Provider context-window 错误映射 | typed error 与稳定 error code 均映射为 `LLMContextWindowError` |
| 预算与完整入口 | tool schema 成本、usage 向上校准、模型级持久化恢复以及无工具入口零开销均有自动化断言 |
| 巨大 tool result | 原始事件不变；模型视图中间截断与紧急占位均保持 call/result 配对并落入预算 |
| 摘要安全 | 首压输入有界、context error 两次收缩、摘要输出二次验算、FIFO 硬边界均有覆盖 |
| Provider 超窗恢复 | 输出前只恢复一次，失败候选不落盘，成功重试只提交一个 checkpoint |
| checkpoint / 重启 | 无效边界会重建；销毁 Conversation、ContextManager、store 后加载同一 JSONL 不重复摘要 |
| 项目统一门禁 | `scripts/check/run.ps1 -q` 通过；1017 passed / 2 skipped，Rust 138 passed / 1 ignored，release check 通过 |
| 真实 usage | DeepSeek 返回首轮 `prompt_tokens=3692`，usage 进入后续预算链路并随 assistant metadata 持久化恢复 |
| 真实压缩与重启 | 1 次摘要 + 3 次对话调用；checkpoint v2 一次生成、重启后复用，摘要调用总数保持 1 |
| 真实摘要质量与原始事件 | `ORCHID-4729`、`LANTERN-8831` 在摘要、压缩后回复和重启后回复中均保留；72 条合成原始消息事件完整存在 |
