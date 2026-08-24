# 会话级免授权模式与高危兜底 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)、[design.md](./design.md)
- 本索引记录：会话授权模式、高危兜底、输入区与授权卡片交互所依据的外部资料、锁定版本代码和本仓库现状。
- 本索引不记录：完整会话原文、竞品内部实现推测、尚未执行的运行时实验或后续实现验证结果。
- 最近对账：2026-08-24，已与确认需求、确认技术方案、Codex 0.147.0 vendor lock 和当前分支基线对账。

## 当前综合判断

agent-friend 需要在自己的统一授权控制面实现会话模式，并在审批模式之外保留独立的灾难性高危门禁。Codex 完整 Agent 链路在关闭审批时仍可能通过 exec-policy 拒绝危险命令，但 agent-friend 直接使用的 Codex 0.147.0 exec-server `process/start` 只负责按调用方提供的 argv 与 sandbox 启动进程，不提供同等的 Agent 层风险分类。Codex、Claude Code 与 Gemini CLI 的公开实践共同支持“审批模式不拥有最高安全权威”：免审批只是普通决策默认值，独立 sandbox、hook、组织策略或更高优先级 deny rule 仍可约束执行。第一版门禁应使用结构化事实、有限命令解析和只读目标核验，区分明确窄范围清理与广泛或目标不明的灾难性动作；不复制完整竞品分类器，也不声称能识别任意脚本语义。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-24 | Codex 如何区分 sandbox 与 approval，以及如何描述无提示完全访问 | 外部资料 | OpenAI 官方 `Agent approvals & security`，2026-08-24 读取 | 官方资料把 sandbox 与 approval 作为两层控制；危险完全访问取消 sandbox 与审批，并明确提示高风险。 | confirmed | active | https://learn.chatgpt.com/docs/agent-approvals-security |
| E-002 | 2026-08-24 | Codex 在 approval policy 为 never 时是否仍会拒绝危险命令 | 代码 | `openai/codex` `rust-v0.147.0`，`codex-rs/core/src/exec_policy.rs` | `render_decision_for_unmatched_command` 对危险命令在 `AskForApproval::Never` 下返回 `Forbidden`，在交互审批模式下返回 `Prompt`。 | confirmed | active | https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/core/src/exec_policy.rs |
| E-003 | 2026-08-24 | Codex 危险命令分类覆盖哪些可机械识别形态 | 代码 | `openai/codex` main，generic 与 Windows dangerous command tests | 上游覆盖 forced rm、PowerShell `Remove-Item -Force`、CMD `del /f` 与 `rmdir /s /q` 等；公开 issue 同时显示粗粒度分类可能误伤普通清理。 | strongly-supported | active | https://github.com/openai/codex/blob/main/codex-rs/shell-command/src/command_safety/is_dangerous_command.rs；https://github.com/openai/codex/blob/main/codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs |
| E-004 | 2026-08-24 | agent-friend 锁定的 exec-server 是否拥有上述 Agent 层危险命令策略 | 代码 | 产品基线 `b12ddc35ca933ef0118596eb44bfc150612d8265`；`scripts/package/vendor/codex.lock.json` 锁定 `rust-v0.147.0`；0.147.0 exec-server README | exec-server 是进程与文件 RPC 执行器；`process/start` 接收调用方 argv、cwd、env 与可选 sandbox，协议未提供 approval policy 或危险命令决策。agent-friend 因而不能假定上层 exec-policy 自动生效。 | confirmed | active | `../../../scripts/package/vendor/codex.lock.json`；https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/exec-server/README.md |
| E-005 | 2026-08-24 | agent-friend 当前授权生命周期与入口是什么 | 代码 | 产品基线 `b12ddc35ca933ef0118596eb44bfc150612d8265`；approval broker、command/filesystem tools、ApprovalDock | 公共 broker 已为 `session` lifetime 保留类型，但仅启用 `once`；文件与命令都经公共控制面等待，前端授权卡片当前只有拒绝与仅本次允许。 | confirmed | active | `../../../agent/src/agent/approvals/models.py`；`../../../agent/src/agent/approvals/broker.py`；`../../../frontend/src/pages/chat/components/ApprovalDock.tsx` |
| E-006 | 2026-08-24 | 其它 Agent 产品如何暴露高风险权限模式 | 外部资料 | 用户提供的 Codex Desktop 截图；Claude Code permission modes 与 interactive mode 官方文档 | Codex 将权限状态放在输入区并突出高风险选项；Claude Code 允许活跃会话切换模式，进入 bypass 模式需要责任警告，并对非受控 root/sudo 场景保留限制。 | strongly-supported | active | 用户提供截图；https://code.claude.com/docs/en/permission-modes；https://code.claude.com/docs/en/interactive-mode |
| E-007 | 2026-08-24 | 本需求最终采用哪些产品边界 | 决策 | 2026-08-24 用户确认；[requirement.md](./requirement.md) | 只保留每次授权与本会话无需授权；模式随持久会话保存；输入区和授权卡片共享每次进入都触发的二次确认；现有 pending 不批量批准；自动模式仍保留收敛的灾难性高危拒绝。 | confirmed | active | [requirement.md](./requirement.md) |
| E-008 | 2026-08-24 | Codex 如何避免把所有强制删除都当成灾难性动作 | 代码与策略 | `openai/codex` main，Guardian policy 与 template | 高风险判断以不可逆影响、授权范围和实际目标为依据；对精确本地路径的删除应先做只读核验，目标缺失、为空或范围窄时通常不应仅因 `rm -rf` 升为高危。 | confirmed | active | https://github.com/openai/codex/blob/main/codex-rs/core/src/guardian/policy.md；https://github.com/openai/codex/blob/main/codex-rs/core/src/guardian/policy_template.md |
| E-009 | 2026-08-24 | Claude Code 的 bypass 模式是否凌驾于全部安全策略 | 外部资料 | Claude Code permission modes、hooks、settings 官方文档，2026-08-24 读取 | bypass 会跳过普通提示，但针对根目录或用户目录的危险文件操作仍有 circuit breaker；`PreToolUse` hooks 独立于 permission mode，可以继续拒绝工具；managed sandbox 与权限规则还能收紧边界。 | confirmed | active | https://code.claude.com/docs/en/permission-modes；https://code.claude.com/docs/en/hooks-guide；https://code.claude.com/docs/en/settings |
| E-010 | 2026-08-24 | Gemini CLI 如何组合 yolo 与更高层安全策略 | 代码与外部资料 | `google-gemini/gemini-cli` main，policy engine 与默认 policy | yolo 由默认层高优先级 allow-all 实现，但规则可按模式生效，User/Admin 等更高 tier 的 deny 规则会覆盖默认 yolo；官方同时建议用 policy engine 的 allowlist/deny，而非脆弱字符串 blocklist。 | confirmed | active | https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md；https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/policy/policies/write.toml；https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/enterprise.md |
| E-011 | 2026-08-24 | 竞品实践与本仓库边界最终如何落成技术方案 | 决策 | 2026-08-24 用户确认；[design.md](./design.md) | 会话模式使用 versioned metadata，与 one-shot approval lifetime 分离；公共 broker 按 session 线性化；高危策略使用 typed facts、有限命令解析、目标核验和副作用前 fingerprint 复核；任意脚本语义明确保留为残余风险。 | confirmed | active | [design.md](./design.md) |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 任意解释器、脚本文件或多层 shell 包装中的破坏性语义第一版能覆盖到什么程度 | 只解析可证明的有限命令族可以保持确定性；尝试理解任意脚本会形成不可靠的“已安全”承诺 | 设计阶段明确有限解包深度、无法解析时的 `unresolved-destructive-target` 规则和残余风险；实现阶段用跨平台正反例固定边界 | 不阻塞技术方案；属于第一版明确残余风险 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 会话模式与高危门禁由 agent-friend 自己实现，不依赖 exec-server 隐式保护 | E-002, E-003, E-004, E-005 | 当前 | 锁定 exec-server 协议未来正式增加并启用可验证的 approval/exec-policy 能力 |
| 权限模式入口放在输入区，并在授权卡片提供同一高风险切换动作 | E-005, E-006, E-007 | 当前 | 输入区组件约束或真实桌面验收证明该入口不可发现、拥挤或与授权卡片状态冲突 |
| 第一版使用独立于审批模式的确定性灾难性高危策略，不引入 LLM reviewer | E-002, E-003, E-007, E-008, E-009, E-010 | 当前 | 确定性规则无法覆盖已确认事故，且有证据证明模型 reviewer 能以可接受成本稳定补足 |
| 高危策略以结构化请求、有限命令解析、目标规范化和只读核验为依据，不复制完整竞品 classifier | E-003, E-008, E-010 | 当前 | 第一版正反例证明有限解析无法满足 AC-8/AC-9，或现有执行协议提供更可靠的结构化风险事实 |
| 会话 mode 进入 versioned metadata，approval lifetime 继续只开放 once | E-005, E-007, E-011 | 当前 | 会话模式需要进入模型可见事件流，或 typed one-shot decision 无法表达精确请求批准 |
| 卡片组合动作与新请求路由由公共 broker 按 session 线性化 | E-005, E-007, E-011 | 当前 | 实现证据证明现有 broker 无法在不产生平行状态机的前提下满足 AC-5/AC-12 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001 至 E-004、E-008 至 E-010 | 公开官方文档、公开上游源码与仓库 vendor lock | 通过 URL、release tag 和产品基线 SHA 锚定，未复制原文 | 未归档 hash；上游 tag 与本仓库 SHA 可复查 | 公开 |
| E-005 | 当前仓库代码 | 产品基线 `b12ddc35ca933ef0118596eb44bfc150612d8265` | Git SHA | 私有仓库内可访问，不含用户数据 |
| E-006 | 用户提供的临时截图与公开 Claude Code 文档 | 截图未复制进仓库；只保留不含个人信息的交互摘要 | 截图未归档 | 内部会话附件；索引不记录临时绝对路径 |
| E-007、E-011 | 当前任务中的用户确认 | [requirement.md](./requirement.md) 与 [design.md](./design.md) 的确认结果 | 文档状态与变更记录 | 不保存完整会话原文 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-24 | requirement、Codex 0.147.0 vendor lock、上游 exec-policy/exec-server、当前 approval broker 与 ApprovalDock | 一致；区分了 Codex Agent 上层 exec-policy 与 agent-friend 直接调用的低层 exec-server，修正了最初把“兜底”误解为模式 fail-closed 的判断 |
| 2026-08-24 | Codex Guardian、Claude Code bypass/hooks、Gemini CLI yolo/policy engine | 一致；三方均支持把审批模式与更高安全策略分层，且 Codex 证据要求以实际目标和影响范围避免误伤窄范围删除 |
| 2026-08-24 | requirement、design、evidence decision impact 与未决问题 | 一致；已将 mode persistence、broker owner、卡片原子动作和 classifier 边界固化为确认方案，仅保留任意脚本语义作为显式残余风险 |
