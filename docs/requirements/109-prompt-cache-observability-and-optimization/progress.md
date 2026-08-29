# 109 · Prompt cache 可观测性与优化基线 - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] T1：扩展 provider usage 归一化，支持 cache read/write/other/total、来源、推导和 accounting mode。
- [x] T2：实现版本化、隐私安全的 `PromptCacheObservation` 与结构/结果解释矩阵。
- [x] T3：在 stream/non-stream 统一模型调用边界产生 observation，正确处理 retry、失败与降级。
- [x] T4：覆盖 Conversation、上下文压缩、memory extraction/reconciliation 的运行语义，并在 session 诊断保留同一 observation。
- [x] T5：实现 `scripts/prompt-cache-report/` 双端工程入口、JSON/Markdown 报告和聚合数学。
- [x] T6：补齐 usage、observation、入口、隐私、兼容和报告的确定性回归。
- [x] T7：运行受影响定向测试与 backend/frontend/rust 完整项目门禁。
- [x] T8：在单次授权后采集真实 baseline，回写机会排序、替代解释和后续需求分诊。
- [x] T9：扩展 final-wire witness 的 item role/domain 与精确断点、lost-prefix 诊断。
- [x] T10：实现 session 跨用户回合 exact lineage、comparison scope 和跨进程分类级恢复。
- [x] T11：补齐 system/persona/tools/messages/runtime/settings/model-route 变化标记及跨回合损失估算。
- [x] T12：扩展报告的 cross-user-turn rebase token 贡献与主要断点域分段。
- [x] T13：完成连续无工具回合、断点域矩阵、session 续开/应用重启和隐私确定性 AC，并重跑门禁。

## AC 验证

- [x] AC-1 Usage 归一化矩阵。
- [x] AC-2 缓存读取/写入 stream、non-stream、持久化与聚合完整性。
- [x] AC-3 请求结构与 provider 结果组合诊断。
- [x] AC-4 全生产入口关联与责任边界证据。
- [x] AC-5 token 加权、请求级、coverage、分段和 unknown 聚合数学。
- [x] AC-6 JSON/Markdown 工程报告。
- [x] AC-7 隐私、旧 provider/session 和 schema 兼容。
- [x] AC-8 绑定 revision/runtime/provider/model/窗口的真实 baseline。
- [x] AC-9 机会排序与后续需求分诊。
- [x] AC-10 provider 请求、上下文治理、工具协议、恢复和回答正确性门禁。
- [x] AC-11 连续无工具独立用户回合 exact final-wire comparison。
- [x] AC-12 persona/system、runtime、Skill/tools、settings、model-route 与 message rewrite 断点域矩阵。
- [x] AC-13 session 续开与应用重启的 exact/domain-only 范围和降级状态。
- [x] AC-14 跨回合 lineage 隐私、rebase token 贡献和断点原因聚合。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-29 | 完成需求 109 的现状盘点、外部官方实践调研和证据索引；用户确认统一观测、工程聚合、baseline 与机会排序范围 | 基线 commit `dc97ad8bfd6657f8fa23cc10eb70e64f70723b43`；未调用真实 LLM |
| 2026-08-29 | 从最新 `origin/main` 创建并预留 `feature/109-prompt-cache-observability-and-optimization`；落盘 `requirement.md`、`evidence-index.md` | 分支已推送；实现尚未提交 |
| 2026-08-29 | 用户确认技术方案并授权继续实现；落盘 `design.md` 与本进度表 | 真实 baseline 仍受 per-run 授权约束；先执行确定性实现与验证 |
| 2026-08-29 | 完成 `LLMUsage` read/write/other/total 互斥口径、`ModelCallTraceContext`、`PromptCacheObservation v2`、stream/non-stream emission 与 failure-safe 降级 | OpenAI inclusive、Anthropic exclusive、DeepSeek hit/miss、明确零/缺失/推导 fixture 通过；observation 不序列化 request/messages/tools |
| 2026-08-29 | Conversation、系统主动轮、工具错误继续、上下文压缩、memory extraction/reconciliation 注入运行语义；assistant `context_usage` 保存同一 observation 子树 | 桌面、IM、voice、API 与主动运行共用 Conversation/LLMClient 边界；外部 voice HTTP 代理不冒充本项目 provider 调用 |
| 2026-08-29 | 新增 `scripts/prompt-cache-report/` JSON/Markdown 双端入口；现有真实 `prompt-cache-smoke` 直接携带 v2 observation | 固定日志 fixture 验证 `50 / 400 = 12.5%` token 加权比例、`1 / 2 = 50%` 请求比例、write/unknown coverage、分段与损坏/旧 revision 处理 |
| 2026-08-29 | 定向回归与完整项目门禁通过 | 定向 `181 passed`；最终 backend ruff/mypy + `2302 passed, 4 skipped`；frontend `670 passed` + production build；Rust `222 passed, 1 ignored` + release check。bilibili atexit、CSS pseudo-element、chunk size 与 Rust dead-code 为既有非阻断 warning |
| 2026-08-29 | 本机只读运行报告入口 | 当前旧日志中 v2 observations=`0`，报告正确显示比例 unavailable；报告绑定 commit `dc97ad8` 与包含未提交文件的 overlay SHA-256。未把零样本或需求 102 历史 smoke 当作 109 baseline。T8、AC-8、AC-9 等待真实 baseline 单次授权 |
| 2026-08-29 | 用户补充跨独立用户回合硬要求并授权本需求真实 LLM 调用；需求/方案回溯 | 原实现的 final-wire witness 随 `RunContinuationProjection.close()` 销毁，无法支撑 cross-user-turn exact comparison；新增 T9-T13、AC-11 至 AC-14，完成前保持 `IN_PROGRESS` |
| 2026-08-29 | 完成跨用户回合 exact lineage、跨进程分类级恢复、断点域/损失估算、Skill/tools 变化标记和报告分段 | 受影响回归 `200 passed`；Conversation 连续无工具回合与重启、系统轮 anchor 隔离、persona/system/runtime/Skill/tools/messages/settings/model-route、隐私和聚合均有确定性断言 |
| 2026-08-29 | 执行 16 次 DeepSeek 真实请求（14 个保留成功样本，另 2 个来自首次工具 smoke 尝试） | exact repeat 四组 probe 约 98.6%～99.8%；工具重组 `0/9843`，严格 append `9344/9508=98.28%`；跨用户回合为 `runtime` rebase、`4608/13929=33.08%`、估算损失 `4639` tokens。首次工具 probe 因 `max_tokens=64` 截断，harness 调至 256 后闭环 |
| 2026-08-29 | 最终 overlay 三阶段项目门禁通过 | backend ruff/mypy + `2319 passed, 4 skipped`；frontend `670 passed` + production build；Rust `222 passed, 1 ignored` + release check。bilibili atexit、CSS highlight、chunk size 与 Rust dead-code 为既有非阻断 warning |
