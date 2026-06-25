# 030 · Agent 行为契约评测正式化 - 进度

## 总体状态

COMPLETED

## 任务列表

### M30.1 包骨架与 case 迁移

- [x] T1. 新建 `agent_eval/` 正式包骨架与 `pyproject.toml`
- [x] T2. 迁移 C1-C4 case fixtures 到 `agent_eval/cases/`
- [x] T3. 接入 root workspace / root dependencies / pytest testpaths
- [x] T4. 将 spike 从 root workspace 成员移除，并在 spike README 标记 archived

### M30.2 loader / schema / judge / selection

- [x] T5. 实现 `schema.py`
- [x] T6. 实现 `loader.py` 与 case set digest
- [x] T7. 实现 deterministic `judge.py`
- [x] T8. 实现 CLI selection 纯函数
- [x] T9. 补 loader / judge / selection 单测

### M30.3 runner / report / baseline / CLI

- [x] T10. 迁入正式 `runner.py`
- [x] T11. 实现 report / baseline JSON writer 与 reports / baselines 目录选择
- [x] T12. 实现 `python -m agent_eval` CLI
- [x] T13. 补 report / baseline schema 单测

### M30.4 scripts / docs / 门禁

- [x] T14. 新增 `scripts/agent-eval/run.sh` 与 `run.ps1`
- [x] T15. 更新 `scripts/README.md`
- [x] T16. 新增 / 更新 `agent_eval/README.md`、`reports/README.md`、`baselines/README.md`
- [x] T17. 更新 `docs/explorations/agent-evaluation/README.md`
- [x] T18. typecheck 纳入 `memory_eval/ agent_eval/` 并修复暴露问题
- [x] T19. 跑非 LLM 验证：help、agent_eval 单测、typecheck、check
- [x] T20. 经用户授权跑最小真实 LLM smoke

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-25 | 创建 `progress.md`，进入 Phase 3 | 从已确认的 `design.md` 执行 |
| 2026-06-25 | 完成 `agent_eval` 包骨架、case 迁移、loader / judge / selection / runner / report / CLI、脚本与文档更新 | 已跑 `uv run pytest agent_eval -q`，16 个非 LLM 测试通过；后续继续跑 help / typecheck / check |
| 2026-06-25 | 完成非 LLM 验证 | `./scripts/agent-eval/run.sh --help` 通过；`./scripts/typecheck/run.sh` 通过（213 files）；`./scripts/check/run.sh` 通过（后端 577 tests + 前端 16 files / 103 tests）。真实 LLM eval 未运行，遵守授权约束 |
| 2026-06-26 | 完成授权 smoke 验证并关闭需求 | `./scripts/agent-eval/run.sh --contract C4 --limit-cases 1 --note "smoke formal agent_eval"` 通过，C4-01 1/1 pass，报告写入 `agent_eval/reports/2026-06-25T17-40-38-a6a5ca8-C4-limit1.json` |
