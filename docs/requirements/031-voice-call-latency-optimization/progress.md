# 031 · voice-call-latency-optimization - 进度

## 总体状态

IN_PROGRESS

## 任务列表

- [x] M31.1 latency trace helper 与字段口径
- [x] M31.2 voice_bridge control / callback / LLM proxy 观测
- [x] M31.3 agent_bridge / agent / memory 首文本和 retrieve 观测
- [x] M31.4 前端 RTC 步骤拆分与冷启动并行化
- [x] M31.5 preflight / warmup
- [x] M31.6 gated voice 策略实验配置
- [x] M31.7 UI 状态细化
- [ ] M31.8 Windows smoke、issue 028 回填

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-26 | 创建 Phase 3 任务清单 | 开始实现；先做 latency trace 与冷启动编排 |
| 2026-06-26 | 完成 latency trace 主链路 | voice_bridge control / LLM proxy、agent_bridge OpenAI route / encoder、agent memory / context / 首个 LLM 文本均打 `voice_latency` 日志 |
| 2026-06-26 | 完成冷启动并行化 | 前端 RTC 适配层拆成 preflight / prepare / join / capture / publish；`joinRoom ok` 后并行 `startAgent` 与本地音频 publish |
| 2026-06-26 | 完成低风险 warmup 与实验开关 | memory / jieba warmup best-effort；voice latency 实验开关默认全关 |
| 2026-06-26 | 完成自动化回归初测 | 后端 targeted ruff / pytest、voice_bridge 集成、前端 typecheck / eslint / targeted vitest 已通过；Windows 真链路 smoke 待跑 |
| 2026-06-26 | 全量门禁通过 | `./scripts/check/run.sh` 全过：ruff / mypy / pytest 589 passed / frontend eslint + token guards + tsc + vitest 115 passed |
