# 102 · Provider transcript 前缀复用 - 进度

## 总体状态

COMPLETED

## 任务列表

- [x] T1：在 provider 层冻结 completed assistant turn，并保证 retry/partial attempt 不串联。
- [x] T2：实现 transport-specific final-wire witness，替换当前 pre-transform request fingerprint 声明。
- [x] T3：实现 run-local continuation projection、generation 与 correctness-first rebase。
- [x] T4：把动态 runtime/advisory 改为带 provenance 的 host user snapshot，并保持 tool-result 邻接。
- [x] T5：接入 ContextManager 预算、compaction、tool projection、恢复与生命周期失效边界。
- [x] T6：补齐 usage、prefix relation、rebase reason 与隐私安全诊断。
- [x] T7：完成 DeepSeek/Kimi/OpenRouter/DeepSeek Vision 确定性回归和 prompt-cache smoke harness。
- [x] T8：运行定向测试、`scripts/check` 与经单次授权的真实 DeepSeek AC-7 smoke。

## AC 验证

- [x] AC-1 严格前缀：fake provider 最终 wire 的第 N+1 请求严格延长第 N 请求。
- [x] AC-2 transcript 保真：单步、多步、并行、失败工具及 reasoning replay 保持原始协议。
- [x] AC-3 动态上下文刷新：新 snapshot 追加且 supersede 旧值，不原地改写前缀。
- [x] AC-4 安全重建矩阵：compaction/history、model/provider、tools、settings、attachments 均有稳定 disposition/reason。
- [x] AC-5 生命周期与恢复：完成/取消/失败/edit/new turn/session/restart 不复用旧 run projection；same-request retry 冻结。
- [x] AC-6 上下文治理不绕过：长历史、巨大工具结果和 compaction 仍经过 ContextManager。
- [x] AC-7 DeepSeek 真实受控工具续轮：第二请求 `cached_input_tokens / prompt_tokens >= 95%`。
- [x] AC-8 无缓存降级：usage 缺失、明确零命中和无缓存 provider 均正常完成。
- [x] AC-9 Provider 回归：DeepSeek、DeepSeek Vision、Kimi、OpenRouter 请求/工具/reasoning/usage 通过。
- [x] AC-10 可观测性：run/request/generation/relation/rebase/usage 可关联且不记录敏感正文。
- [x] AC-11 项目门禁：核心行为有自动化覆盖，完整检查通过。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-24 | Phase 3 启动；确认在既有 `feature/102-provider-transcript-cache-reuse` 分支续传；保留需求调查 overlay 作为实现起点 | 尚未运行新的真实 LLM；历史 provenance 仍是 `6a657d1 + overlay` |
| 2026-08-24 | completed provider turn、run-local projection、host snapshot、final-wire witness、usage/诊断和真实 smoke harness 均已落地 | actual HTTP MockTransport 已验证 DeepSeek Vision/Kimi request body、strict extension 与 same-request retry；敏感正文不进入 witness repr/诊断 |
| 2026-08-24 | 完整项目门禁通过 | backend：ruff + mypy + `2174 passed, 4 skipped`；frontend：`608 passed` + production build；Rust：`222 passed, 1 ignored` + release check。现有 bilibili-api atexit warning 与构建 chunk/dead-code warning 不影响门禁结果 |
| 2026-08-24 | T8 仅剩 AC-7 真实 DeepSeek 工具续轮 | 尚未发起新的外部 LLM 请求；等待本次 `4` 次短输出 DeepSeek smoke 的 per-run 授权 |
| 2026-08-24 | 经用户本次 per-run 授权完成 4 次 DeepSeek 强制工具请求 | `deepseek-v4-flash-vision-exp`；184 条稳定消息；26 个工具；strict continuation probe 为 cached `43,008` / prompt `43,197` = `99.5625%`，wire relation=`strict_extension`、common prefix=`185`，AC-7 通过。对照 rebase probe 也复用了 184-item 公共前缀：`45,312 / 45,509 = 99.567%`，说明 wire relation 与 provider cache hit 是独立维度 |
| 2026-08-24 | 用户确认需求验收完成，并授权合入主干、推送远端 | T1-T8、AC-1 至 AC-11、完整项目门禁与真实 DeepSeek AC-7 证据均已齐备；总体状态收口为 `COMPLETED` |
