# 095 · Agent 运行恢复与终止语义 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：真实事故、当前代码行为、既有需求约束、外部 Agent harness 行为和用户确认的范围选择。
- 本索引不记录：完整用户输入、完整模型输出、凭据、隐藏推理、原始私密日志或尚未执行的真实模型实验结果。
- 最近对账：2026-08-21，需求与技术方案状态均为 `CONFIRMED`；实现、确定性门禁、项目全量门禁和冻结的真实事故模型清单均已完成，待用户最终验收。

## 当前综合判断

当前 Runtime 把多类模型可修正错误升级为宿主停止或 run failure，已经同时违反既有“工具失败不应中断对话”的产品底线和成熟 Agent harness 的模型可见错误恢复模式。095 采用响应项独立闭合、`RespondToModel/Fatal` 错误分层和 `InProgress/Completed/Interrupted/Failed` turn 四态；错误次数只保留为观测信号，不再拥有终止权限。非法调用仍必须在执行与授权前拒绝，真实取消、显式资源预算、外部不可恢复错误和内部基础设施失败继续作为终止边界。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-21 | 本次用户可见失败由宿主在第二次 malformed 后主动终止 | 运行时观测 | 本机 21:38～21:39 运行；模型 `deepseek/deepseek-v4-flash-vision-exp`；原始日志未归档 | 两次 `filesystem_search` 都包含 3 个合法必填字段和 1 个未知字段；中间一次合法目录读取成功，第二次 malformed 后 run 以 `malformed_tool_call` 失败。非法搜索没有执行或授权。 | confirmed | active | [requirement.md](./requirement.md) |
| E-002 | 2026-08-21 | malformed 次数被实现为 run 级终止计数 | 代码 | product HEAD `5508e832df8e1a4546b954fd6ff117e68cbe1a06`；`conversation.py` blob `4e11707154e88f2369b66f1da7975cc28f7f7ef2` | `malformed_tool_call_retries` 在工具循环外初始化，首次失败后加一，第二次失败直接抛 `MalformedToolCallError`；合法工具结果不会改变该终止授权。 | confirmed | active | [`agent/src/agent/conversation.py`](../../../agent/src/agent/conversation.py) |
| E-003 | 2026-08-21 | `no_progress` 和普通用户动作标记会强制关闭工具循环 | 代码 | 同一 product HEAD；`policy.py` blob `b59c7faa76a5d9d47aefee12648444ad88c56d22` | 默认阈值为 3；重复错误、空结果或重复证据触发无工具收尾。批次内停止布尔值一旦置位不会被同批后续成功撤销。任一 `requires_user_action` 结果也会进入无工具收尾。 | confirmed | active | [`agent/src/agent/tool_runtime/policy.py`](../../../agent/src/agent/tool_runtime/policy.py)、[`agent/src/agent/conversation.py`](../../../agent/src/agent/conversation.py) |
| E-004 | 2026-08-21 | 无有效模型输出的若干情况缺少同轮恢复 | 代码 | 同一 product HEAD；`client.py` blob `b7239c142a3928881f2cac4777c38e77d4de72a7` | 空响应和输出额度耗尽直接抛错；`LLMStreamTimeoutError` 的 retry limit 为 0，因此即使没有可见文字或工具调用也会结束当前 run。 | confirmed | active | [`llm_providers/src/llm_providers/client.py`](../../../llm_providers/src/llm_providers/client.py)、[`agent_bridge/src/agent_bridge/errors.py`](../../../agent_bridge/src/agent_bridge/errors.py) |
| E-005 | 2026-08-21 | 可修正工具错误可以作为失败结果返回模型并继续采样 | 外部资料/代码 | `openai/codex` commit `7f9832d0d08c0e2fd56a02421477756474fec95d` | 参数解析错误进入 `RespondToModel`；非 Fatal 工具错误转换为 `success=false` 的 function output，随后设置 `needs_follow_up=true`。助手消息与工具调用作为独立响应项按顺序持久化。 | confirmed | active | [handlers](https://github.com/openai/codex/blob/7f9832d0d08c0e2fd56a02421477756474fec95d/codex-rs/core/src/tools/handlers/mod.rs#L82-L88)、[failure output](https://github.com/openai/codex/blob/7f9832d0d08c0e2fd56a02421477756474fec95d/codex-rs/core/src/tools/parallel.rs#L72-L86)、[follow-up](https://github.com/openai/codex/blob/7f9832d0d08c0e2fd56a02421477756474fec95d/codex-rs/core/src/stream_events_utils.rs#L289-L386) |
| E-006 | 2026-08-21 | 安全、任务内的多步工作应持续推进，停止条件应对应真实边界 | 外部资料 | OpenAI model guidance，2026-08-21 抓取 | 官方指引要求为 Agent 明确自主与审批边界，让安全、任务内工作持续进行，并在外部写入、破坏性、付费或范围扩张等边界前停止；响应输出项应分别保留和验证。 | strongly-supported | active | [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model) |
| E-007 | 2026-08-21 | 项目既有需求同时存在“不应中断”和“重复失败停止”的冲突 | 文档 | 需求 005、065 均为 `CONFIRMED` | 005 要求工具失败时对话不中断；065 要求连续相同失败达到阈值后停止重复消耗。095 需要保留显式资源预算，替代失败次数拥有的终止权限。 | confirmed | active | [005](../005-engine-tool-calling-and-web-search/requirement.md)、[065](../065-trustworthy-read-tools-and-tool-runtime/requirement.md) |
| E-008 | 2026-08-21 | issue 054 修正了错误披露，但把第二次 malformed 固化为 run failure | 历史记录 | issue 054；commit `1b19a56ca4f4b09bdf996f4f3576cba72929a7e1` | 054 保证非法调用不执行、不授权并准确报错；其终止策略解决了“伪装完成”，但没有提供模型可见失败结果和持续恢复。 | confirmed | retained | [issue 054](../../issues/054-malformed-tool-call-misreported-as-runtime-boundary/README.md) |
| E-009 | 2026-08-21 | 095 采用一个需求同时覆盖工具和模型请求恢复 | 决策 | 用户在阶段 1 review 中确认 | 空响应、输出额度耗尽和流超时与 malformed 一起处理；不拆分平级需求。 | confirmed | active | [requirement.md](./requirement.md) |
| E-010 | 2026-08-21 | 不设置基于错误次数的宿主终止，并保留独立响应项和失败活动 | 决策 | 用户在阶段 1 review 中确认 | 错误次数仅作观测/软提示；助手文字与失败工具调用独立保留；失败活动和继续处理状态遵循统一 thread/item 语义。 | confirmed | active | [requirement.md](./requirement.md) |
| E-011 | 2026-08-21 | 技术方案整体采用响应项、模型可见错误和 turn 四态语义 | 决策 | 用户在阶段 2 review 中确认“都对齐 Codex” | 可绑定错误形成失败工具结果，不可绑定错误形成模型恢复事实；等待审批保持 InProgress；turn 只以 Completed、Interrupted 或 Failed 终结，具体边界使用 reason code。 | confirmed | active | [design.md](./design.md) |
| E-012 | 2026-08-21 | 模型响应是有序 output item 集合，输出预算同时包含隐藏推理和可见输出 | 外部资料 | OpenAI Responses API reference，2026-08-21 抓取 | response `output` 是按模型响应顺序返回的 item 数组；`max_output_tokens` 同时约束 reasoning 与 visible output，未完成响应另有 `incomplete_details`。这支持逐项保留和把“推理耗尽预算但无可用 item”作为 incomplete response 恢复。 | confirmed | active | [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create) |
| E-013 | 2026-08-21 | 已确认方案已落成可恢复 item、advisory-only progress、请求恢复和精确 Runtime 终态 | 实现/测试 | 分支 `feature/095-resilient-agent-run-recovery`，基线 `5508e832df8e1a4546b954fd6ff117e68cbe1a06`，当前为未提交实现树 | 可绑定非法调用闭合为未执行/未授权失败结果；不可绑定输出进入模型专用 rejection；失败次数不停止；Runtime 保留 reason、item/执行/模型调用和预算事实。定向回归 292 passed。 | confirmed | active | [progress.md](./progress.md)、[design.md](./design.md) |
| E-014 | 2026-08-21 | 事故与安全边界已固化为 C26 可重复行为契约 | 评测资产/确定性测试 | `c26-agent-run-recovery.yaml`；case-set C1-C26 共 228 条；当前 digest `5f55b5ec40a2fac3202f2943f86e1baf62e252e872fee123a31d78cf01b6cedf` | 六条 case 覆盖 DeepSeek 连续未知字段、Kimi 连续缺参、mixed batch、文字后 malformed、副作用不执行/不授权/不重放和显式预算。agent_eval 126 passed。 | confirmed | active | [`agent_eval/cases/c26-agent-run-recovery.yaml`](../../../agent_eval/cases/c26-agent-run-recovery.yaml)、[progress.md](./progress.md) |
| E-015 | 2026-08-21 | 项目全量门禁通过 | 验证 | backend/frontend/rust 三段标准 check，2026-08-21 | 最终 backend lint/mypy + 2002 passed/4 skipped；frontend lint/typecheck + 596 tests + production build；Rust 215+6 tests 与 release check 通过。 | confirmed | active | [progress.md](./progress.md) |
| E-016 | 2026-08-21 | 事故模型和安全 holdout 的首次真实复验完成 | 真实 LLM 评测 | 六项冻结清单；DeepSeek V4 Flash Vision 5 case、Kimi K2.5 1 case；正常共 9 次 provider request | 两条事故目标直接通过；mixed batch、文字纠错、显式预算直接通过；副作用 holdout 原始自动 fail 仅因文案未重复“未授权”，trace 已机械证明未执行、未授权、未重放，按 judge 假阴一致性修复闭环且未重跑。单次样本只作为观察证据，不宣称统计稳定性。 | confirmed | active | [progress.md](./progress.md)、[`C26-01 report`](../../../agent_eval/reports/2026-08-21T15-48-26-5508e83-C26-01.json)、[`C26-02 report`](../../../agent_eval/reports/2026-08-21T15-49-12-5508e83-C26-02.json)、[`C26-03 report`](../../../agent_eval/reports/2026-08-21T15-49-55-5508e83-C26-03.json)、[`C26-04 report`](../../../agent_eval/reports/2026-08-21T15-50-32-5508e83-C26-04.json)、[`C26-05 report`](../../../agent_eval/reports/2026-08-21T15-51-09-5508e83-C26-05.json)、[`C26-06 report`](../../../agent_eval/reports/2026-08-21T15-52-55-5508e83-C26-06.json) |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 可绑定失败事务在不同 Provider 历史协议中的兼容性 | 本次 DeepSeek/Kimi 单样本均接受原始 arguments + 配对 result / 其它 Provider 或重复样本仍可能不同 | 后续出现新 Provider 事故时按原模型增加独立 case；需要统计置信度时再授权重复采样 | 当前目标已验证；保留单样本证据边界 |
| 已有响应项后的断流能否由未来 Provider 原生续接 | 当前只能准确失败 / 后续 transport 可提供 response id 续传 | 后续 Provider 暴露稳定 resume token 后增加定向实验 | 不阻塞当前实现；本期禁止盲目重放 |
| 输出额度耗尽时是否需要调整显式 output 配置 | 简洁恢复提示足够 / catalog 需要新增可信 output limit | fake transport 先固定不改设置的机制；真实模型复验观察恢复质量 | 不阻塞当前实现；无可信上限前不调整 |
| 早停在实际历史中的发生频率 | 只发生在已知 malformed / 还存在未记录的 completed 早停 | 增加终态 reason 可观测性后查询新的有界样本 | 不阻塞当前修复；现有日志无法可靠统计 completed 早停 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| malformed 作为模型可见失败继续 run，不再抛第二次终止错误 | E-001、E-002、E-005、E-008、E-010 | 当前 | 发现继续会绕过审批、重复副作用或破坏 Provider 协议 |
| `no_progress` 降级为诊断和软提醒 | E-003、E-005、E-007、E-010 | 当前 | 有决定性证据证明某类循环无法由显式资源边界安全约束 |
| 普通 `requires_user_action` 不拥有全局停止权 | E-003、E-005、E-010 | 当前 | 证明某个协议已进入必须等待用户且无法并行推进的真实状态 |
| 空响应、输出额度耗尽和有效输出前流超时纳入 095 | E-004、E-006、E-009 | 当前 | Provider 能力迫使其成为独立、不可共同交付的架构变化 |
| 助手文字与失败工具活动独立保留 | E-005、E-010 | 当前 | 发现独立保留会泄漏不可展示内容或破坏审计/安全边界 |
| 使用响应项独立闭合、`RespondToModel/Fatal` 分层与 turn 四态 | E-005、E-006、E-011 | 当前 | 现有 Provider 协议无法承载且降级投影仍不能保持同等行为语义 |
| 缺少可靠关联的模型输出写入 `model_output_rejection`，不伪造工具事务 | E-005、E-011 | 当前 | 找到跨 Provider 都支持的可靠原生 response item 关联协议 |
| 输出预算恢复默认不猜测或放大 `max_tokens` | E-006、E-011、E-012 | 当前 | 模型目录获得经验证的显式 output limit 与恢复配置 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001 | 本机应用日志与用户提供截图 | 未归档；仅保留脱敏事实摘要 | 未校验 | 含会话和本机运行信息，不进入仓库 |
| E-002～E-004 | product HEAD 与文件 blob | Git 仓库 | commit 与 blob hash | 内部源码 |
| E-005 | OpenAI 官方公开仓库 | 固定 commit URL | commit 固定 | 公开 |
| E-006、E-012 | OpenAI 官方文档 | 稳定文档 URL | 采集日期已记录 | 公开 |
| E-007～E-011 | 当前仓库文档与用户确认 | 当前需求目录和既有文档 | Git 历史待后续提交 | 内部项目资料 |
| E-013～E-015 | 当前 feature 分支实现、C26 fixture 与本地门禁输出 | 当前工作树、agent_eval 与 progress | 未提交实现树；命令、计数与日期已记录 | 内部源码与本地验证，不含真实模型输出 |
| E-016 | agent_eval 本轮六份 report | `agent_eval/reports/2026-08-21T15-48-26...15-52-55-*` | report 含模型、case-set digest、trace、judge 与 note；C26-05 原始 fail 保留 | 含真实模型输出但使用 synthetic fixture，不含用户文件正文或凭据 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-21 | requirement、事故日志摘要、product HEAD、Codex 官方源码、既有 005/065/054 | 首次建立；需求范围与用户四项确认一致，技术方案未开始落盘 |
| 2026-08-21 | requirement、design、证据账本、官方响应项/turn 语义与用户阶段 2 确认 | 技术方案落盘；四个设计开放点已收敛为 response item 失败闭合、不可绑定恢复事件、有效输出边界和 turn 四态；补录输出 item 顺序与 output budget 官方语义 |
| 2026-08-21 | 实现树、C26、定向测试与三段项目全量门禁 | 实现和 deterministic 验证完成；C26 已固化，真实 DeepSeek/Kimi 运行仍严格标为待授权、未执行 |
| 2026-08-21 | 冻结真实运行清单、六份 report、C26-05 judge 假阴一致性修复 | 清单全部完成；自动结果与人工复核已区分；未把单次通过写成稳定通过，未重跑假阴 case |
