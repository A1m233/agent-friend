# 132 · 证据索引

## 用途与范围

记录本需求已完成的单次事故诊断、源码检查与官方参考。本文不保存诊断包正文、完整用户会话、工具参数或凭据；并非正式模型评测或桌面验收结果。

## 当前综合判断

- 运行时确认首次失败发生在成功工具结果之后的模型请求：上游 HTTP 500，服务端消息为 TLS handshake EOF；自动重试一次后仍失败。
- 随后一次继续请求在模型调用前失败；日志栈指向恢复资格校验。
- 基础源码的恢复校验与生命周期事件不兼容；继续入口同时禁用工具，因此仅修校验不能满足继续完成任务。
- 应分开处理请求重试、任务继续及用户可见状态；阶段 2 已确认按可靠提交边界自动恢复，方案见 [design.md](./design.md)。

## 证据账本

| ID | 主张与来源锚点 | 观察及限制 | 基础 / 置信度 / 状态 |
| --- | --- | --- | --- |
| E-001 | 用户提供 `agent-friend-diagnostics-20260916-015655.zip`；`sessions/session-001.json` 事件 417–430（0-based），`logs/agent_bridge.log` 6831–6895、6914–6929；2026-09-16 01:46:16–01:46:48 +08:00 | 工具预检查成功后模型请求失败；重试一次后终止。随后 `/continue-tool-result` 运行在 0 次模型调用时因 AgentError 失败。日志与 session 记录一致；不把两次运行误算为同一通知重复 | runtime-supported / confirmed / active |
| E-002 | 构建 `20260914T175952Z-fcab8eb17236-87a25f26`，基础 SHA `fcab8eb172360408244c95fddde4d709f744d32f`；`agent/src/agent/conversation.py` 的 `continue_after_tool_error`、`_validate_tool_error_continuation`；`agent/src/agent/runtime/__init__.py` 的执行入口 | 先写 run_started，再校验末事件必须为 run_error；校验未跳过 run_started/run_terminal。构建记录 dirty=true，未提交 overlay 未知，基础源码不能冒充精确运行输入 | code-supported + runtime-supported / strongly-supported / active |
| E-003 | 同一基础 SHA；`continue_after_tool_error`；`agent/tests/test_tool_calling_integration.py::test_continue_after_tool_error_reuses_result_without_reinvoking_tool` | 入口以 tools=None 生成整理文字，测试明确断言工具为空；这不等价于恢复完整任务。这里只读源码与测试，未声称运行过该测试 | code-supported / confirmed（源码语义） / active |
| E-004 | [Codex 官方源码固定 SHA](https://github.com/openai/codex/tree/8a30bc31efcb942eddaed96f13c7858d75726482)；`codex-rs/core/src/session/turn.rs:1546`、`responses_retry.rs:49`；2026-09-16 查阅 | 请求重试使用历史与工具结果，并保留工具能力；重试策略有错误类型、次数和传输降级边界。不是任意副作用 exactly-once 保证 | code-supported / confirmed（官方开源实现） / active |
| E-005 | 同一 Codex SHA；`codex-rs/app-server/src/bespoke_event_handling.rs:1054`；`codex-rs/tui/src/chatwidget/protocol.rs:452`、`streaming.rs:372` | 区分 willRetry 与终态；TUI 以状态栏展示重连，并对同 turn 的 error/turn-completed 重复错误去重。闭源 Desktop 未复现；不能据此保证不同运行的错误一定合并 | code-supported / confirmed（协议与 TUI） / active |
| E-006 | [官方配置样例](https://learn.chatgpt.com/docs/config-file/config-sample)；2026-09-16 查阅；本仓库 `llm_providers/src/llm_providers/client.py` | Codex request_max_retries 默认 4，stream_max_retries 默认 5；本项目网络重试默认 1。两层语义不同，不能直接相乘套用 | official-documentation + code-supported / confirmed / active |
| E-007 | 本项目基础 SHA 同 E-002；`LLMClient._stream_with_network_retries`、`Conversation._run_tool_loop_body`、`sessions/recovery.py`、`frontend/src/pages/chat/components/MessageList.tsx` | Provider 已有取消感知退避、禁用 SDK 默认重试及临时工具参数重置约定；可见文字后断流会抛出专用异常，前端继续按钮却仅支持 upstream_transient/rate_limit 且要求成功工具。正常工具循环等待事件的 max_attempts 另有硬编码 2；重启工具事务已有 RUNNING/UNKNOWN/COMPLETED/LOST 处理，需复用 | code-supported / confirmed（当前源码） / active |
| E-008 | 同一 Codex SHA；`core/src/session/turn.rs:1569–1652,2362–2388,2509–2527,2839–2865,2990–3029`；`core/src/stream_events_utils.rs:300–382`；2026-09-16 补查 | 网络流错误进入外层可重试判断，没有“已显示文字即禁止重试”的门槛。退出流读取后先收集已启动工具结果，再返回错误；重试从最新历史重建带工具的 prompt。文字 delta 的客户端投影与完整 item 的历史提交是不同边界；未完成片段不能冒充已完成模型历史。这是源码证明，未运行 Codex 测试、未实测 Desktop，不保证任意新工具请求不会产生语义重复 | code-supported / confirmed（限定源码控制流） / active |
| E-009 | 同一 Codex SHA；`core/tests/suite/stream_no_completed.rs::retries_on_early_close`；`core/tests/suite/stream_error_allows_next_turn.rs::continue_after_stream_error` | 官方测试分别断言 SSE 缺少 completed 后再次请求成功，以及失败后释放当前轮并接受下一轮输入。首个测试不是“部分文本 + 已执行副作用”的完整复现；第二个测试证明新轮可启动，不证明闭源 Desktop 的手动继续按钮会合并旧轮 | code-supported（测试契约，只读） / confirmed（断言范围） / active |

## 结论演进

初步建议仅修恢复校验与合并错误提示；补查 E-003 后发现工具禁用导致恢复目标不完整，因此扩大为完整任务续行、请求重试与状态一致性。初步方向作为不足的历史判断保留，当前以需求确认范围为准。

阶段 2 初步提出“部分文字后断流默认手动继续”。E-008 进一步表明 Codex 不按是否出现文字一刀切，其自动恢复依赖完整 item、工具结果与历史的提交边界。该初步方案尚未确认，不作为最终设计；后续方案应优先建立安全提交边界内的自动恢复，再对无法确定的执行状态明确暂停。跨运行尝试合并为一个任务状态区域仍是本产品已确认需求，不能声称由 Codex Desktop 实测证明。

## Artifact 与覆盖

- 原始 ZIP 和截图由用户在当前任务提供，保留在用户原始本机位置；未复制到公开仓库。通过附件文件名与下方校验定位，不依赖私有绝对路径理解结论。
- ZIP SHA-256：`0f8472711d6011c818a9ecd4cc8e70e5a4d3c6e221bbe23ad35fae5d62d52bb6`，2026-09-16 使用 `Get-FileHash -Algorithm SHA256` 核对。
- 检查命令：`inspect-agent-friend-diagnostics/scripts/inspect_bundle.py <用户提供 ZIP>`；已执行 CRC、路径及结构检查。
- ZIP 63 entries，format 1 / revision 13，outcome complete，collector failures 0；请求并包含 1 个 session。complete 只说明采集器成功，不等于全历史完整。
- 包内 session 是有界投影；部分日志仅保留尾部。本次事故对应事件与异常栈均在保留窗口内；原始 provider 请求/推理正文不在本次证据中。
- install integrity consistent；source dirty=true 是精确源码身份缺口，不是损坏结论。
- 未执行真实 LLM、游戏任务、桌面复现或新增模型评测。原始附件只在当前用户环境可复查。

## 未决问题与下一步

- 实现已按完整模型响应提交边界接入；后续如要支持逐 item 的可靠提交，需要另行验证 provider 语义，不能把展示结束当成提交。
- 当前代码与自动化测试能证明机制，不能替代真实桌面视觉或真实模型任务完成率结论。用户已接受当前验证范围并要求合入。

## 实现验证补充（2026-09-17）

| ID | 来源锚点 | 观察及限制 | 状态 |
| --- | --- | --- | --- |
| E-010 | `llm_providers/tests/test_stream_recovery.py`；`agent/tests/test_tool_calling_integration.py` 的 shared budget、desktop disconnect、continue、system text 和 finalization 测试 | 五次重试；模型步骤内语义修复和传输失败共用预算；已完成 A 后恢复可执行 B，未提交文本和参数不混入下一次结果。全部使用受控假流，不调用真实模型 | automated / confirmed |
| E-011 | `agent/tests/test_task_continuation.py`；`agent_bridge/tests/test_meta_channel_routes.py::TestContinueToolResultEndpoint` | 实际事务协调器与 Runtime 处理生命周期、来源、并发拒绝和持久化；running/unknown 不盲目恢复，lost 仍保留未知副作用语义 | automated / confirmed |
| E-012 | `frontend/src/stores/taskRecovery.test.ts`；`frontend/src/stores/conversation.test.ts`；`progress.md` 验证记录 | 显式关联恢复链合并显示；SSE/push 撤回一致；拒绝请求无额外占位；历史刷新/分页保留恢复状态，耗时不包含手动等待。不是 Desktop 视觉实测 | automated / confirmed |
| E-013 | `scripts/check/run.ps1` 的 backend/frontend/rust 三阶段与最终专项复验；详见 `progress.md` | 全量回归及后续小范围修改的针对性验证分别记录，不把不同代码阶段拼成同一测试运行。当前为未提交工作区；基线 HEAD `47dfe3ee7d1563713fbbe8b0b2c2b5c852c06d9e`，分支 `feature/132-task-interruption-recovery` | automated / confirmed；用户已接受当前交付范围 |

## 决策影响与对账

- E-001～E-003 支持 AC-04、AC-06～AC-09；E-004～E-006 支持恢复边界和重试口径的讨论。
- 2026-09-16：需求与设计已确认并落盘；未实施。证据仅支持上述范围，不扩大为 Desktop 实测或精确安装包源码复现。
- 2026-09-17：实现已落地，E-010～E-013 支持 AC 的确定性机制验证；当前进度及门禁结果以 `progress.md` 为准。原始诊断与 Codex 对照仍作为历史依据保留，不覆盖为本次运行结果。
