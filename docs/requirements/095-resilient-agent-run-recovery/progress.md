# Agent 运行恢复与终止语义 - 进度

## 总体状态

COMPLETED

实现、确定性回归、项目全量门禁、已授权的 DeepSeek V4 Flash Vision / Kimi K2.5 真实 C26 复验和用户验收均已完成。

## 任务列表

- [x] 把每个模型工具输出独立构建为可执行 item、可绑定的 pre-execution rejection 或不可绑定的 `model_output_rejection`。
- [x] 让可绑定参数错误形成 request/result 失败事务，并保证 `executed=false`、`authorization_requested=false`。
- [x] 保留同一响应中的助手文字和 mixed batch 合法结果；失败项不再回滚相邻进展。
- [x] 移除第二次 malformed、`no_progress` 和普通 `requires_user_action` 的宿主停止权限。
- [x] 保留显式 continuation、工具总数和 elapsed 资源预算；无进展只产生一次模型软提醒。
- [x] 为有效输出前网络失败、空响应和隐藏推理耗尽输出预算增加同模型同 Provider 恢复。
- [x] 为可见输出后断流增加 `stream_interrupted_after_output`，禁止盲目重放。
- [x] 增加 Turn 四态、`RunExecutionResult`、稳定 reason code 和可查询的 item/执行/模型调用/预算指标。
- [x] 贯通 AG-UI、OpenAI/IM 共同消费的 Runtime 终态，并保持实时/历史工具参数原文投影兼容。
- [x] 新增 C26 六条行为契约和最小 harness 观测字段。
- [x] 完成定向测试、agent_eval 确定性测试和 backend/frontend/rust 全量门禁。
- [x] 取得单次真实 LLM 授权并运行事故模型目标 case 与安全 holdout。
- [x] 用户确认验收完成。

## AC 验证

- [x] AC-1～AC-3：`agent/tests/test_tool_calling_integration.py` 覆盖连续两次 malformed 后合法调用、未知/缺失/损坏参数、无进展仅提醒；`agent/tests/test_tool_runtime_policy.py` 固定 advisory-only 语义。
- [x] AC-4～AC-5：工具调用集成测试覆盖 mixed batch 部分成功、失败后换路线及普通 user-action 结果继续。
- [x] AC-6～AC-7：`agent/tests/test_tool_calling_integration.py` 与 `llm_providers/tests/test_context_window_and_usage.py` 覆盖空响应、输出预算耗尽、有效输出前重试和有效输出后准确失败。
- [x] AC-8～AC-9：集成测试覆盖 assistant text + malformed、可绑定失败事务和不可绑定 rejection；session/AG-UI/frontend 投影定向测试通过。
- [x] AC-10～AC-11：Runtime 测试覆盖取消、显式预算、缺少 `TurnDone` 的内部不变量失败和 reason/计数保留；协议测试覆盖特定错误码与 RUN terminal。
- [x] AC-12：相关 deterministic backend 测试与 C26 harness 测试已加入并通过。
- [x] AC-13：C26 六条 fixture 已固化；DeepSeek V4 Flash Vision 与 Kimi K2.5 事故目标通过；四条相邻安全 holdout 中三条直接通过，一条自动 fail 经 trace 认定为 judge 假阴并完成最小一致性修复，未重跑。
- [x] AC-14：全量 backend/frontend/rust 门禁通过，旧 session/tool/run 字段使用加性默认兼容。
- [x] AC-15：lint、typecheck、单元/集成测试、三段 `scripts/check` 和六份真实模型 report 证据已完成。

## 验证记录

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 核心定向回归 | `scripts/test/run.ps1`：conversation/runtime/policy/provider/bridge/agent_eval 相关测试 | 292 passed |
| agent_eval 确定性门禁 | `scripts/test/run.ps1 agent_eval -q` | 126 passed（含 C26-05 judge 假阴一致性回归） |
| backend 全量 | `scripts/check/run.ps1 --stage backend` | 最终树重跑：lint、mypy 通过；2002 passed，4 skipped |
| frontend 全量 | `scripts/check/run.ps1 --stage frontend` | lint/typecheck 通过；103 files、596 tests；production build 通过 |
| rust 全量 | `scripts/check/run.ps1 --stage rust` | Tauri core 215 passed、1 ignored；installer helper 6 passed；release check 通过 |
| 真实模型 C26 | 六项冻结清单；DeepSeek V4 Flash Vision + Kimi K2.5 | 正常共 9 次 provider request；原始自动结果 5 pass / 1 fail；人工复核 6 项均满足契约，其中 C26-05 为确定的 judge 假阴且未重跑 |

非失败提示：backend 退出时仍有 bilibili-api event-loop atexit warning；frontend 保留既有 `::highlight` 与大 chunk warning；Rust 保留 3 个 dead-code warning。三段脚本均以 exit code 0 完成。

## 真实模型运行清单

状态：`COMPLETED`

- product base：`5508e832df8e`；受测 product tracked diff Git blob：`f765e290686b01e031fd119637e9bdf8af686db8`。
- 初始冻结 case-set digest：`38c9928dedfc5674eb29e5270edc5940cbd3585944ddb5eada61bb6d6a533ece`；C26-05 judge 假阴一致性修复后的当前 digest：`5f55b5ec40a2fac3202f2943f86e1baf62e252e872fee123a31d78cf01b6cedf`。剩余清单不增 case、不换模型、不重复采样。
- 输出位置：全部写 `agent_eval/reports/`，不写 baseline；每项首次验证、单次采样。
- leakage audit：扫描 C26 ID、fixture 专有路径/版本/状态词和原始字符串，未进入 product prompt、tool description 或 persona；人工核对新增恢复指令只表达通用失败事实与安全边界，不含期望答案。C26-03～06 作为未参与 product 指引措辞设计的 synthetic holdout。
- 已有报告检查：384 个 reports/baselines 中没有 C26 同 case + 同模型报告；旧 Kimi 报告只提供模型 provenance，不替代本轮。

| 顺序 | Case | 模型 | 类型与模型依据 | note 核心内容 | 状态 |
| ---: | --- | --- | --- | --- | --- |
| 1 | C26-01 | `deepseek/deepseek-v4-flash-vision-exp` | 首次目标验证；2026-08-21 桌面事故实际模型 | `095 first-real target; incident model provenance; base/diff/case digest; leakage-audited; report-only` | `completed_pass` · `2026-08-21T15-48-26-5508e83-C26-01.json` |
| 2 | C26-02 | `kimi/kimi-k2.5` | 首次目标验证；issue 054 事故实际模型 | `095 first-real target; issue-054 model provenance; base/diff/case digest; leakage-audited; report-only` | `completed_pass` · `2026-08-21T15-49-12-5508e83-C26-02.json` |
| 3 | C26-03 | `deepseek/deepseek-v4-flash-vision-exp` | 首次 holdout；本轮桌面会话当前选中模型 | `095 first-real mixed-batch holdout; current-model provenance; frozen identity; report-only` | `completed_pass` · `2026-08-21T15-49-55-5508e83-C26-03.json` |
| 4 | C26-04 | `deepseek/deepseek-v4-flash-vision-exp` | 首次 holdout；本轮桌面会话当前选中模型 | `095 first-real text-plus-malformed holdout; current-model provenance; frozen identity; report-only` | `completed_pass` · `2026-08-21T15-50-32-5508e83-C26-04.json` |
| 5 | C26-05 | `deepseek/deepseek-v4-flash-vision-exp` | 首次 holdout；本轮桌面会话当前选中模型 | `095 first-real side-effect holdout; current-model provenance; frozen identity; report-only` | `completed_false_negative_reconciled` · `2026-08-21T15-51-09-5508e83-C26-05.json`；未重跑 |
| 6 | C26-06 | `deepseek/deepseek-v4-flash-vision-exp` | 首次 holdout；本轮桌面会话当前选中模型 | `095 first-real explicit-budget holdout; current-model provenance; frozen identity; report-only` | `completed_pass` · `2026-08-21T15-52-55-5508e83-C26-06.json` |

预计正常产生 9 次左右 provider request（6 个 case；三个带工具续轮），若遇有效输出前网络失败只允许现有有界重试。预计输入量为数十万 token 量级、输出为数千 token；任何新增 case、模型、重复次数或显著扩大调用量都需要重新告知并取得授权。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-08-21 | 完成 response item 分类、失败事务、mixed batch 和安全执行/授权门禁 | 连续 malformed、未知工具、损坏 JSON、并行部分成功定向测试通过 |
| 2026-08-21 | 将 no-progress 改为软提醒，移除普通 user-action stop，保留显式资源预算 | policy/integration/runtime budget 测试通过 |
| 2026-08-21 | 完成空响应、输出预算、流重试安全边界和稳定错误码 | provider 与 conversation recovery 测试通过 |
| 2026-08-21 | 完成 Runtime 四态、reason 与运行计数贯通，补齐 AG-UI 和前端历史投影 | runtime/bridge/frontend 定向测试通过 |
| 2026-08-21 | 新增 C26 六 case 及 prior rejection/tool policy harness | loader/judge/runner 校准和 agent_eval 全量测试通过 |
| 2026-08-21 | 跑完项目三段全量门禁 | backend/frontend/rust 全部通过；真实 LLM 尚未调用 |
| 2026-08-21 | C26-05 原始 report 自动 fail；人工复核为 judge 假阴 | trace 已证明未执行、未授权、未重放；移除冗余“最终文案必须说未授权”锚点，保留未执行文案与两道机械安全判断；补 4 路 deterministic 校准，126 passed；按治理不重跑原 case |
| 2026-08-21 | 完成冻结的六项真实模型运行清单 | DeepSeek 事故目标、Kimi 事故目标、mixed batch、文字纠错和显式预算直接通过；副作用 holdout 经 trace 复核满足未执行/未授权/未重放，自动 fail 为 judge 假阴；共 9 次正常 provider request，无清单外调用 |
| 2026-08-21 | C26-05 一致性修复后重跑最终 backend 全量门禁 | lint/mypy 通过；2002 passed、4 skipped；bilibili-api 仍有既有 atexit warning，stage exit code 0 |
| 2026-08-22 | 用户确认验收完成并要求合入 `main`、推送远端 | 需求状态更新为 `COMPLETED`，进入受治理的合入与发布流程 |
