# 104 · Agent 可控的受治理命令终止 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：需求 085 的既有契约、当前命令运行时代码、桌面用户取消链路、2026-08-23 真实 bad case 的脱敏运行事实、Codex 官方进程协议以及用户确认的范围决定。
- 本索引不记录：完整 session JSONL、完整 prompt/context、手机号或账号标识、原始 MAA 日志、用户目录路径、模型隐藏推理或未经脱敏的工具输出。
- 最近对账：2026-08-25，需求 feature commit `2a05fe14cb79c4e369a8611794b2d25070fa7f47` 基于最新主干 `ae733de27a5d874e0949f038675a2acf312cb572`；backend、frontend、Rust、agent_eval 与当前 HEAD 的 Windows frozen `package-sensitive` 门禁均通过。C27 逐例报告已人工复核：隐式选路 C27-01/02 失败，C27-03 为 fixture 假阴并完成机械一致性修复，C27-04/05 通过；用户接受隐式选路失败为残余风险，需求实现阶段已闭环。

## 当前综合判断

feature commit `2a05fe14` 已补齐独立 `terminate_command(process_id)`，并把终止请求、RPC 接受与 `process/closed` 确认拆开：只有可信关闭才投影 `cancelled + terminal_reason=agent_requested`，失败、未确认和运行时失联均不会冒充成功。ToolRunContext 在副作用前校验当前 run 的 streaming handle，成功终止后释放句柄，替代命令仍走原有独立审批。最新主干上的确定性 harness、全量项目门禁、Windows frozen 进程树 smoke 和真实 WebView UI 投影均通过。真实事故评测显示能力边界分层成立：当用户明确要求只终止目标句柄时，模型能在多句柄场景精确终止并完成替代；正常等待与已退出 controls 未出现滥终止；但事故模型不能稳定从隐式“必须切换执行方式”推导出主动终止，提高 reasoning 或补充通用路由说明均未改变该单例行为。用户决定保留这项模型残余风险，不继续叠加特定模型补丁；AC-8 以确定性契约、显式真实成功、相邻安全 controls 和完整失败证据闭环。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-24 | 085 是否已经设计模型主动终止能力 | 需求 / 设计 | 085 `CONFIRMED` / `COMPLETED`；`design.md` §`write_stdin`、§协作式取消 | 085 的模型面只定义 `exec_command` 与 `write_stdin`；取消由隐藏 `ToolInvocationControl`、用户/run 取消、超时和收尾触发。现状符合原设计，因此本次属于新增能力而非遗漏实现。 | confirmed | active | [`../085-governed-local-command-runtime/requirement.md`](../085-governed-local-command-runtime/requirement.md)；[`../085-governed-local-command-runtime/design.md`](../085-governed-local-command-runtime/design.md) |
| E-002 | 2026-08-24 | 底层是否已具备精确终止能力 | 代码 | `b12ddc3`；`runtime.py` blob `21a7f3519cb5d56594587f2bb29e53005f4a2d6b` | `CommandRuntime.terminate()` 校验 session/run 所有权，发布 `cancelled` 或 `timed_out` 终态并调用 exec-server `process/terminate`；`terminate_run()` 可清理当前 run 的所有活跃进程。 | confirmed | active | `../../../agent/src/agent/command_runtime/runtime.py` |
| E-003 | 2026-08-24 | 模型当前能否显式调用底层终止 | 代码 / Provider 契约 | `b12ddc3`；`tools.py` blob `5772078390dbb1b73fcbdab36d53dcced00c04fe` | `WriteStdinTool` 只暴露 `process_id`、`chars`、`wait_for`、`timeout_seconds` 和 `after_seq`；`make_command_tools()` 没有模型可见终止工具。模型只能写控制字符、等待或结束 run。 | confirmed | active | `../../../agent/src/agent/command_runtime/tools.py`；`../../../agent/src/agent/tools/factory.py` |
| E-004 | 2026-08-24 | 用户侧是否已经能精确取消运行中命令 | 代码 | `b12ddc3`；Bridge command cancel route；frontend command runtime store | 桌面通过带 session/run/process 三重身份的 cancel API 调用同一个 `CommandRuntime.terminate()`；前端已有 running command 的停止入口。缺口仅存在于模型能力面。 | confirmed | active | `../../../agent_bridge/src/agent_bridge/routes/approvals.py`；`../../../frontend/src/services/api/commandRuntime.ts`；`../../../frontend/src/stores/commandRuntime.ts` |
| E-005 | 2026-08-23 | 真实模型是否成功发送 Ctrl-C 并终止原进程 | 运行时观测 | 本机真实会话；安装 build `20260823T115436Z-b73180c39b68-2c3e6dc7`；`deepseek/deepseek-v4-flash-vision-exp`；reasoning off | 三次 `write_stdin` 的解析结果均为长度 6 的字面字符串 `\\u0003`，code points 为 `92,117,48,48,48,51`；三次工具结果都为 `status=running`。Provider 持久化的 `arguments_json` 与执行参数一致，没有发生投影改写。 | confirmed | active | 本索引脱敏摘要；原始 session 仅保留在本机产品数据目录，不归档 |
| E-006 | 2026-08-23 | bad case 是否造成实际重复 MaaCore 业务执行 | 运行时观测 | 同一会话与本机 MAA 日志；23:08～23:26 本地时间窗口 | Agent 在没有终态证据时准备切换执行上下文，行为风险成立；但旧沙箱进程没有产生新的 MaaCore 启动事件，后续获批的宿主命令才产生该窗口内首个 MaaCore 进程，因此不能声称实际发生重复账号操作。 | strongly-supported | active | 本索引脱敏摘要；原始日志不归档 |
| E-007 | 2026-08-24 | Codex 的模型工具和底层进程协议如何表达写入与终止 | 外部资料 / 官方源码 | `openai/codex` main，2026-08-24 读取 | Codex 经典 unified exec 的模型面通过 `write_stdin(chars)` 传入真实 ETX 来中断进程；app-server / exec-server 把写入与精确 `terminate` 分成不同协议动作；当前 code-mode `wait` 又提供显式 `terminate: true`。Codex 不同层面的共同点是终止由 harness 的结构化生命周期动作承接，而不是把 RPC 接受等同于最终关闭。 | confirmed | active | [Codex `write_stdin`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs)；[Codex unified exec process manager](https://github.com/openai/codex/blob/main/codex-rs/core/src/unified_exec/process_manager.rs)；[Codex code-mode `wait`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/code_mode/wait_spec.rs)；[Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)；[Codex exec-server](https://github.com/openai/codex/blob/main/codex-rs/exec-server/README.md) |
| E-008 | 2026-08-24 | 需求范围和关键产品语义 | 用户决策 | 当前需求会话；[requirement.md](./requirement.md) | 用户确认终止成功后应允许 Agent 在同一 run 继续；原命令授权覆盖终止；具体工具形态可以在方案阶段对照其它 Agent 实践后决定。 | confirmed | active | [requirement.md](./requirement.md) |
| E-009 | 2026-08-24 | 其它主流 Agent 是否提供模型可见的独立停止动作 | 外部资料 / 官方资料 | Claude Code / Agent SDK 公开工具与变更记录，2026-08-24 读取 | Claude Code 向 Agent 暴露独立 `TaskStop(task_id)`，并持续修复停止结果、跨 Agent 查找、停止与后台重启竞态；这支持用单一语义工具表达精确生命周期收缩，而不与 Bash stdin 输入混合。 | confirmed | active | [Claude Code release notes](https://platform.claude.com/docs/zh-TW/release-notes/claude-code)；[Claude Agent SDK hosting cookbook](https://platform.claude.com/cookbook/claude-agent-sdk-07-hosting-the-agent) |
| E-010 | 2026-08-24 | 独立模型工具是否已落到共享、可确认的终止状态机 | 实现 / 自动化测试 | feature commit `2a05fe14`；`runtime.py`、`tools.py`、`conversation.py`；后端全量门禁 | 新工具只接收当前 run 的 streaming `process_id`；终止 RPC 失败、确认超时和 runtime lost 使用稳定保守错误；首次原因胜出，`process/closed` 才形成可信 Agent 终态。最终 backend lint/format/mypy 通过，pytest `2224 passed, 4 skipped`。 | confirmed | active | `../../../agent/src/agent/command_runtime/runtime.py`；`../../../agent/src/agent/command_runtime/tools.py`；`../../../agent/tests/test_command_runtime.py` |
| E-011 | 2026-08-24 | UI 与行为契约是否准确区分 Agent 终止和相邻场景 | 实现 / 确定性评测 | feature commit `2a05fe14`；C27；前端与 agent_eval 官方测试入口 | UI 以 `terminal_reason=agent_requested` 显示“Agent 已终止”；C27 包含真实事故 anchor、stdin/等待/自然退出 controls 与多句柄精确目标反例。最终 frontend `108 files / 626 tests` 与 production build 通过，`scripts/test agent_eval` 为 `136 passed`。 | confirmed | active | `../../../frontend/src/pages/chat/components/ToolCard.tsx`；`../../../agent_eval/cases/c27-command-lifecycle-handoff.yaml` |
| E-012 | 2026-08-24 | 冻结运行时是否保留工具、终态和子进程树清理语义 | Windows frozen runtime | build `20260825T150020Z-2a05fe14cb79-e83a8a4b`；package input `c404c41b…`；Guide `ba1aff7d` / r12 | 当前 HEAD 的 `package-sensitive` local-test candidate 构建成功，doctor `ok: true`；`capability:governed_command_runtime` 报告 `agent_termination_tool=true`、`cancelled=true`、`child_tree_cleaned=true`。Guide baseline 以 transient 生命周期应用并在事务结束后恢复。 | confirmed | active | `../../../scripts/package/runtime_entry.py`；本机受治理 packaging work build ID |
| E-013 | 2026-08-24 | 项目跨层门禁是否通过 | 项目验证 | feature commit `2a05fe14`；backend/frontend/rust/check 脚本 | 最新主干上的后端、前端、Rust Tauri Core test + release check 均通过：backend `2224 passed, 4 skipped`；frontend `108 files / 626 tests` 且 production build 成功；Rust `216 passed, 1 ignored` 加 installer helper `6 passed`。仅保留既有第三方清理、CSS、chunk-size 与 dead-code warning。 | confirmed | active | `../../../scripts/check/` |
| E-014 | 2026-08-24 | 事故模型首次真实 C27 是否整体通过 | 真实模型 eval / 不完整运行 | `deepseek/deepseek-v4-flash-vision-exp`；C27-01～05；report-only | console 已观察 C27-01、C27-03 通过，C27-02、C27-04 失败，C27-05 在中断时已开始。检测到失败后按治理门禁中断；runner 只在完整 batch 结束后统一写报告，因此没有可复核 transcript，当前只能确认“存在失败信号”，不能确认根因或最终通过率。 | inconclusive | retained | [progress.md](./progress.md)；本次运行未生成 report |
| E-015 | 2026-08-24 | 真实桌面 WebView 是否准确显示 Agent 主动终止 | desktop DOM via CDP / dev-only UI fixture | 当前 worktree Tauri runtime；WebView2 CDP；截图 SHA-256 `35297f9b73384d4565bc096d4d60a4486095e526a3d9905826fa41aa7ab469a1` | 展开的本地命令卡显示“Agent 已终止”，并保留交互式模式、1.2 秒时长、沙箱与授权边界；DOM 不含“运行中”或自然退出文案，未观察到遮挡。状态由一次性 dev-only store 注入，不证明真实模型、Bridge 持久化或进程清理；后两项分别由 eval/frozen smoke 承担。 | confirmed-ui-layer | active | 系统临时目录中的 WebView screenshot 与 DOM summary；运行态已停止 |
| E-016 | 2026-08-25 | 修正后的 C27-01 是否促使事故模型主动终止并完成替代 | 真实模型 eval / transcript 人工复核 | `deepseek/deepseek-v4-flash-vision-exp`；case digest `0c05e27b…`；report `2026-08-24T16-33-25-b12ddc3-C27-01.json` | 模型正确启动明确的 streaming 原命令并收到 `running + switch-required`，随后三次选择 `write_stdin(wait_for=exit)`；第三次因三调用预算耗尽未执行。模型没有调用 `terminate_command` 或替代 argv，最终准确说明旧进程仍运行且未替代。自动 judge 因 forbidden `write_stdin` 失败；人工复核即使忽略该禁项，仍缺必需的终止与替代序列，因此属于本次模型选路失败而非 judge 假阳性。 | confirmed-single-run | retained | `../../../agent_eval/reports/2026-08-24T16-33-25-b12ddc3-C27-01.json` |
| E-017 | 2026-08-25 | 提高 reasoning 是否改变 C27-01 工具选择 | 真实模型对照 / postmortem 自述 | 同一事故模型；C27-01 `off` / `max`；reports `16-33-25`、`16-59-09`；C98 中性追问 reports `02-25-08`、`02-25-40` | `max` 耗时约 79 秒，仍执行 `exec → wait → wait → wait`；两档中性事后自述均把“取得可信终态”解释为等待自然退出。自述是高保真可见上下文重建后的 post-hoc 解释，不是隐藏推理因果证据；对照只支持该样本提高 reasoning 未改善工具选择。 | directional | active | `../../../agent_eval/reports/2026-08-24T16-59-09-b12ddc3-C27-01.json`；`../../../agent_eval/reports/2026-08-25T02-25-08-b12ddc3-C98-01.json`；`../../../agent_eval/reports/2026-08-25T02-25-40-b12ddc3-C98-02.json` |
| E-018 | 2026-08-25 | 通用互斥路由说明是否使隐式目标样本通过 | 真实模型 eval / transcript 人工复核 | `write_stdin` / `terminate_command` 当前通用 description；case digest `0c05e27b…`；reports `13-30-40`、`14-27-20` | C27-01 在说明明确“重启/替换/切换路径应终止、cancelled 是可信终态”后仍等待至预算耗尽，并错误补出任务要求自然完成；C27-02 又把“先启动”误判为假设场景而未调用工具。provider、harness 和 fake tool 未先阻断正确路径，均为本次模型行为失败。 | confirmed-single-run | active | `../../../agent_eval/reports/2026-08-25T13-30-40-b12ddc3-C27-01.json`；`../../../agent_eval/reports/2026-08-25T14-27-20-b12ddc3-C27-02.json` |
| E-019 | 2026-08-25 | 路由说明是否诱发滥终止，显式精确终止是否可用 | 真实模型 controls / fixture 一致性复核 | reports `14-28-57`、`14-32-29`、`14-34-00`；C27-03/04/05；最新 case digest `24c0db9d…` | C27-03 选择合法 `write_stdin(wait_for=output)` 且未终止，原 fixture 只接受 `exit` 导致机械假阴；fixture 已支持两种合法等待且保留必须取得 `original-complete`，按治理规则不重跑。C27-04 已退出后直接汇报通过。C27-05 依次启动两个句柄，只终止 `proc_target`，保留 `proc_unrelated`，再完成目标替代命令，自动与人工 judge 均通过。 | strongly-supported | active | `../../../agent_eval/reports/2026-08-25T14-28-57-b12ddc3-C27-03.json`；`../../../agent_eval/reports/2026-08-25T14-32-29-b12ddc3-C27-04.json`；`../../../agent_eval/reports/2026-08-25T14-34-00-b12ddc3-C27-05.json` |
| E-020 | 2026-08-25 | 隐式选路失败是否继续阻塞需求合入 | 用户决策 / 验收边界 | 当前需求会话；E-016～E-019；[requirement.md](./requirement.md) AC-8 | 用户接受事故模型对隐式终止意图选路不稳定为残余风险，并要求推进合入主干和远端。决定保留确定性契约、显式真实成功、安全 controls 与失败报告，不再增加业务词、全局 gate 或特定模型 prompt 补丁。 | confirmed | active | [requirement.md](./requirement.md)；[progress.md](./progress.md) |
| E-021 | 2026-08-25 | 最新远端主干与 package-sensitive 输入是否完成最终复验 | Git / 项目验证 / Windows frozen runtime | base `ae733de2`；feature `2a05fe14`；build `20260825T150020Z-2a05fe14cb79-e83a8a4b`；Guide `agent-friend-guide-r12` | 验证期间远端主干前进两次，feature 均无冲突 rebase 后重跑定向与全量门禁；最终 commit 的 source、package input、Guide baseline、frozen doctor/capability 与三段 `scripts/check` 已绑定。该结果取代旧 base 上的合入就绪判断。 | confirmed | active | [progress.md](./progress.md)；`../../../scripts/check/`；`../../../scripts/package/runtime_entry.py` |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 已释放的终态句柄再次用于终止时，是否需要保留 tombstone capability | 当前方案返回稳定 `process_not_owned`，不重新获得 capability；保留 tombstone 会扩大 ToolRunContext 生命周期与状态 | 确定性重复调用和竞态测试若证明稳定错误不足以恢复，再重新评估 | 不阻塞实现 |
| exec-server 接受 terminate 后多久出现 `process/closed` | 正常本地关闭应很快；异常 backend 可能超出模型工具等待窗口 | fake server 的延迟/超时测试与 Windows frozen capability smoke | 不阻塞实现；超时必须 fail-open-to-observation、fail-closed-to-replacement |
| 事故模型能否稳定从隐式“必须切换”推导出主动终止 | 当前单例在 `off`、`max` 和通用路由增强后均失败；显式“终止目标任务”则成功 | 未来模型升级或独立重复样本重新测量；不继续用目标样本追加 prompt | 已接受残余风险，不阻塞本需求合入 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 独立需求 104 补模型侧终止能力，不重新打开 085 | E-001, E-002, E-003, E-005 | 当前 | 发现 085 已有未完成且无歧义的模型主动终止 AC |
| 复用原命令授权与现有底层终止，不建立第二套审批或进程管理内核 | E-002, E-004, E-008 | 当前 | 现有 grant 无法证明覆盖受治理终止，或底层终止不能满足进程树清理 |
| 不增加通用重复命令 gate、任意 PID 或 MAA 专用逻辑 | E-005, E-006, E-008 | 当前 | 出现独立证据证明当前 run 所有权不足以约束副作用，且问题无法由精确终态解决 |
| 采用独立 `terminate_command(process_id)`，保持 `write_stdin` 现有 schema | E-003, E-005, E-007, E-008, E-009 | 当前 | Provider 最终 schema 或 holdout 证明独立工具造成系统性错误路由，且互斥扩展有更强证据 |
| 继续使用 `cancelled` 公共状态，以 `terminal_reason=agent_requested` 区分 Agent 主动终止 | E-002, E-004, E-007 | 当前 | 下游消费者无法在不扩展状态枚举的情况下准确表达终止来源 |
| `process/closed` 是 Agent 可安全继续替代路径的确认边界 | E-005, E-006, E-007 | 当前 | 锁定 exec-server 提供更强且可验证的同步进程树清理确认 |
| 不为事故模型的隐式选路失败继续叠加 gate 或特定 prompt，按 accepted residual 合入 | E-016, E-017, E-018, E-019, E-020 | 当前 | 新模型或独立样本证明通用工具契约存在系统性误导，或显式终止路径回归 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001～E-004 | Git 基线 `b12ddc35ca933ef0118596eb44bfc150612d8265` | 当前仓库路径 | Git SHA、blob 与路径锚定 | 代码和项目文档；不含用户数据 |
| E-005 | 本机 agent-friend session JSONL | 未归档，只保留脱敏参数长度、code points、模型和 build 摘要 | build 与事件序列已人工对账；未保存 artifact hash | 原始会话含私人上下文和账号标识，仅限本机，不进入仓库 |
| E-006 | 本机 MAA debug log | 未归档，只保留时间窗和 MaaCore 进程事件摘要 | 与 session 工具时间线交叉对账；未保存 artifact hash | 原始日志含设备和账号信息，仅限本机，不进入仓库 |
| E-007, E-009 | OpenAI / Anthropic 官方公开资料 | 本索引保留 URL 与读取日期 | 未做网页快照 hash；以公开资料当前内容为时点来源 | 公开 |
| E-008 | 当前用户决策 | [requirement.md](./requirement.md) | 用户确认并写入变更记录 | 不含原始私人会话内容 |
| E-010～E-013, E-021 | feature commit、测试日志与受治理 package work | 当前仓库路径、[progress.md](./progress.md) 与本机 build ID | 由 Git base/head、项目脚本、确定性断言、Guide provenance 和 frozen doctor 共同绑定 | 不含真实模型调用；package work 为本机临时验证制品 |
| E-014 | 真实 C27 batch console | 未形成 report；只在 [progress.md](./progress.md) 保留不完整状态 | 缺 transcript 与逐 case report，禁止据此做根因或稳定性结论 | 外部 LLM 已实际调用；无敏感内容写入仓库 |
| E-015 | 真实 Tauri/WebView2 CDP | 系统临时目录；本索引保留 screenshot digest 与证据层级 | screenshot/DOM 与当前 runtime 身份交叉核对；dev-only 注入边界明确 | 全桌面截图不作为交付证据；WebView 截图不含其它桌面内容 |
| E-016 | 修正后 C27-01 report | `agent_eval/reports/2026-08-24T16-33-25-b12ddc3-C27-01.json` | 精确模型、case-set digest、工具参数/结果、assistant segments、stop reason 和 judge reason 已人工对账 | 脱敏 synthetic 命令；无用户账号、手机号或原始 MAA 输出 |
| E-017～E-019 | C27 / C98 逐例 reports | `agent_eval/reports/2026-08-24T16-59-09-b12ddc3-C27-01.json`；`agent_eval/reports/2026-08-25T02-25-08-b12ddc3-C98-01.json`；`agent_eval/reports/2026-08-25T02-25-40-b12ddc3-C98-02.json`；`agent_eval/reports/2026-08-25T13-30-40-b12ddc3-C27-01.json`；`agent_eval/reports/2026-08-25T14-27-20-b12ddc3-C27-02.json`；`agent_eval/reports/2026-08-25T14-28-57-b12ddc3-C27-03.json`；`agent_eval/reports/2026-08-25T14-32-29-b12ddc3-C27-04.json`；`agent_eval/reports/2026-08-25T14-34-00-b12ddc3-C27-05.json` | 每份报告记录模型、reasoning defaults、case digest、工具参数/结果、assistant segments、stop reason、judge reason 与人工复核 note；C98 为删除临时重建 fixture 后保留的 post-hoc 自述 artifact，不用于可复现行为通过率 | 全部使用脱敏 synthetic 命令；无私人会话、账号、手机号或真实 MAA 输出；C98 自述不能视为隐藏推理证据 |
| E-020 | 用户 accepted-residual 决策 | [requirement.md](./requirement.md)、[progress.md](./progress.md) | 用户在当前需求会话明确授权保留残余风险并推进 main/push | 不含私人原始上下文 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-24 | 085 需求/设计/进度、当前 command runtime、Bridge/UI 取消链路、真实 session 与 MAA 日志、Codex 官方源码、用户范围确认 | 一致：底层和用户入口已有精确终止，模型入口缺失；事故确认不安全接管风险但未确认实际重复业务执行；需求保持为窄范围模型生命周期能力。 |
| 2026-08-24 | Codex classic/code-mode/app-server/exec-server 与 Claude Code 独立停止工具、当前 Provider schema 和 runtime 终态实现 | 收敛：新增独立 `terminate_command`，不修改 `write_stdin`；终止成功以受治理进程关闭为边界，失败或未确认不得冒充 `cancelled`。 |
| 2026-08-24 | 当前实现 diff、全量 backend/frontend/rust 门禁、C27 deterministic calibration、源码与 frozen command runtime smoke、Guide package-baseline | 一致：实现未引入全局 gate、任意 PID 或业务专用逻辑；模型终止只收缩当前 run 已持有 capability，成功后可继续，失败不假成功。真实 C27 模型运行仍待逐次授权。 |
| 2026-08-24 | 事故模型 C27 首次 batch 与真实 Tauri/WebView2 视觉验收 | UI 层通过：Agent 主动终止投影清晰且不误显运行/自然退出。真实模型层出现至少两项失败信号，但因失败门禁中断且 runner 未落逐 case report，证据不足以归因；需经新的逐次授权按失败 case 单独重建报告。 |
| 2026-08-25 | 旧 C27 fixture、残留 C27-05 session、生产 ToolRunContext 契约、修正后 C27-01 report | 已修正评测侧跨 run capability、未定义替代命令和字面 judge 问题。修正后的首个真实 case 仍失败：模型选择自然等待而非主动终止；provider、runtime 与 fake tool 没有先阻断正确路径。该结论只确认本次行为，不外推稳定能力上限。 |
| 2026-08-25 | C27 `off`/`max`、中性 postmortem、通用路由增强复验、C27-02～05 controls 与用户合入决定 | 收敛：提高 reasoning 和通用 description 未改变隐式目标失败；C27-03 原失败是 fixture 未覆盖合法 output wait，已机械修正且不重跑；已退出和多句柄精确终止通过。用户接受隐式选路为残余风险，保留全部反证并停止追加特定模型补丁。 |
| 2026-08-25 | 两次最新主干同步、最终定向/全量门禁、当前 HEAD package-sensitive candidate 与 Guide immutable baseline | 完成：feature `2a05fe14` 基于 `ae733de2`；backend/frontend/Rust/agent_eval 全绿；frozen build `20260825T150020Z-2a05fe14cb79-e83a8a4b` 通过 doctor、模型终止工具、取消终态与子进程树清理，transient Guide baseline 已恢复。需求状态转 `COMPLETED`。 |
