# 007 · voice-call · 进度

## 状态

代码级完成，合入 main（**真机语音 E2E 待验证，挂账**）。

## 验证情况（截至合入）

对照 [`test-plan.md §3 合并 main 前的最终清单`](./test-plan.md)：

| 项 | 状态 | 说明 |
|---|---|---|
| §1.1 全量 pytest 全绿 | ✅ | `145 passed`（含 `voice_bridge/tests/integration/test_acceptance_criteria.py` 全部 AC mock 集成测试） |
| §1.4 lint + typecheck 全绿 | ✅ | ruff check + format、mypy strict 均通过 |
| §2 Win 真机 E2E smoke | ⏳ **未做** | 受环境限制（仅有的可用机器在公司内网，不允许公网穿透，无法跑真火山 RTC 链路） |
| AC-9 `data/` / `experiments/` 不动 | ✅ | 见 `git diff main..feature/007-voice-call` |

## 合入决策与挂账

按 `test-plan.md §3`，严格流程要求 §2 真机 smoke 通过 + 用户确认才可合 main。本次是**有意识的破例**：

- **破例原因**：§2 卡在**环境**（无合规可穿透的机器），而非代码质量；预计短期内无法补测。若继续 gate，007 会长期悬挂、与后续需求（008 起）的冲突面持续累积，越拖越难合。
- **降级后的合入门槛**：代码级 + mock 级 AC（§1 / §1.4）全绿即合，**真机语音端到端体验记为待办**。
- **遗留风险**：真火山 RTC 的拨打 / 进房 / ASR / TTS / 挂断全链路体感未经真机验证；`voice_bridge` 为独立包 + 独立进程，不启动它不影响 CLI / agent_bridge 默认行为，故对 main 其余能力风险可控。

## 后续

- 拿到合规可穿透的机器后，按 [`test-plan.md §2`](./test-plan.md) 跑 Win 真机 smoke，补齐 §2 四条验收，回填本文件。
