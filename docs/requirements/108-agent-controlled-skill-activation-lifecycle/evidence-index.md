# 108 · Agent 可控的 Skill 激活生命周期 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：真实用户 bad case 的脱敏事实、当前实现与既有需求约束、同类 Agent 产品和 Agent Skills 标准的公开实践、用户确认的需求边界，以及后续技术方案、实现和验证证据。
- 本索引不记录：完整用户消息、完整模型输出、隐藏 prompt、凭据、原始私密日志、用户主目录内容或尚未执行的真实模型实验结果。
- 最近对账：2026-08-29，需求与候选技术方案均已封存，未进入实现、测试或真实模型复验；需求 076、088 继续作为现行运行时契约。

## 当前综合判断

当前失败不是 Skill 更新失败，而是运行时把“轮初 catalog 内容版本”同时当作“首次激活允许使用的唯一版本”。这保护了确定性，却把冻结点放在了 Skill 尚未参与任务之前。外部标准和多种 Agent 实现普遍采用“元数据先发现、正文激活时加载”的渐进披露，但它们并没有直接解决 agent-friend 已有文件 scope、受治理命令快照和安装原子替换共同要求的完整包一致性。设计调研确认，正确修改需要把 Skill 包提升为用户轮级不可变资源，并增加 installer 到 activation 的可信跨工具协议。用户据此决定封存当前需求；所有方案结论保留为未来候选，不改变当前产品行为。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-28 | 真实用户请求是否因轮内版本变化被迫拆成两轮 | 本地真实会话 | 用户截图与本机 agent-friend session JSONL；约 01:12～01:17 | 用户要求先更新 `maa-daily` 再执行日常；更新从旧版本成功切换到新版本，随后 `skill_read` 返回 `skill_changed_during_turn`，Agent要求用户再发一条消息，下一轮才继续。 | confirmed | active | 原始截图与 session 仅保留在用户本机；仓库只记录脱敏行为事实 |
| E-002 | 2026-08-28 | 当前激活入口为何拒绝更新后的 Skill | 代码 | product HEAD `7def69b819b83b709a8e0313a76db370a4ee7aef`；`agent/src/agent/skills/service.py` | `activate_from_snapshot()` 使用轮初 snapshot 记录的内容版本核对当前文件；不一致时返回 `skill_changed_during_turn`，不会在本轮形成激活。 | confirmed | active | [`service.py`](../../../agent/src/agent/skills/service.py)、[`skill tool`](../../../agent/src/agent/tools/builtin/skills/tool.py) |
| E-003 | 2026-08-28 | 当前需求文档是否有意规定下一轮生效 | 已确认需求/设计 | 需求 076 design、需求 088 requirement | 076 明确版本不一致时不刷新重试；088 AC-8 明确更新期间已开始的轮继续使用原 snapshot，新内容在后续用户轮激活。当前行为符合旧设计，不是偶发实现偏差。 | confirmed | superseded-in-part | [076 design](../076-agent-skill-system/design.md)、[088 requirement](../088-github-skill-update-lifecycle/requirement.md) |
| E-004 | 2026-08-28 | 当前命令资源是否可能晚于正文另行取快照 | 代码 | 同一 product HEAD；`conversation.py`、`command_runtime/skill_snapshot.py` | 运行时会吸收隐式激活；命令路径按 Skill ID 缓存执行快照并在命令准备时复制包。若正文激活与命令快照不共享精确版本绑定，存在说明和资源混版风险。 | confirmed | active | [`conversation.py`](../../../agent/src/agent/conversation.py)、[`skill_snapshot.py`](../../../agent/src/agent/command_runtime/skill_snapshot.py) |
| E-005 | 2026-08-28 | Agent Skills 标准如何划分发现与加载 | 开放规范 | Agent Skills Specification，2026-08-28 抓取 | 所有 Skill 的名称和描述先作为元数据加载；Agent 决定激活后才加载完整 `SKILL.md`，引用资源继续按需读取。标准不要求在任务开始时冻结所有正文。 | confirmed | active | [Agent Skills Specification](https://agentskills.io/specification) |
| E-006 | 2026-08-28 | Codex 如何感知变化并加载 Skill | OpenAI 官方文档/源码 | Codex docs；`openai/codex` commit `430d26b543b219049192de559987b8cf506efacf` | Codex 自动检测 Skill 变化；app-server 发出 `skills/changed` 并支持 `skills/list(forceReload)`。官方源码的 Skill 读取路径在使用时读取所选入口的当前文本，host snapshot 主要固定发现映射而非预冻结全部正文。 | confirmed | active | [Codex Skills](https://developers.openai.com/codex/skills)、[App Server](https://developers.openai.com/codex/app-server)、[source](https://github.com/openai/codex/blob/430d26b543b219049192de559987b8cf506efacf/codex-rs/ext/skills/src/host_outcome.rs) |
| E-007 | 2026-08-28 | Claude Code 如何处理激活与已加载内容 | Anthropic 官方文档 | Claude Code Skills，2026-08-28 抓取 | 常规会话只常驻名称和描述，完整正文在调用时加载；已渲染内容作为一条消息留在会话中，后续不会自动重读文件。激活控制与 `allowed-tools`、权限规则分开。 | confirmed | active | [Claude Code Skills](https://code.claude.com/docs/en/skills) |
| E-008 | 2026-08-28 | OpenCode 是否在激活时解析当前定义并独立检查权限 | OpenCode 官方文档 | OpenCode V2 Skills，2026-08-28 抓取 | 每个模型步骤只广告允许的元数据；模型调用 `skill` 后，运行时解析该 ID 的当前 winning definition、检查权限，再把正文加入会话。HTTP Skill 另以版本字段驱动缓存刷新。 | confirmed | active | [OpenCode Skills](https://opencode.ai/v2/docs/skills/) |
| E-009 | 2026-08-28 | GitHub Copilot 如何处理加载、来源与版本 | GitHub 官方文档 | GitHub Copilot Skills / `gh skill`，2026-08-28 抓取 | Copilot 根据描述选择后把 `SKILL.md` 注入上下文并暴露包内文件；`gh skill` 为安装记录来源、ref 和 tree SHA，支持固定版本及受控更新，并警告审查不可信 Skill。 | confirmed | active | [About Agent Skills](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)、[Add and manage Skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills) |
| E-010 | 2026-08-28 | 108 是否同时覆盖更新与新安装后的同轮使用 | 用户确认 | 本次需求阶段 review | 用户确认：轮初已知 Skill 更新后同轮激活；本轮可信安装结果指向的新 Skill 同轮显式激活；首次激活后按用户轮冻结完整包；权限与副作用门禁保持独立。 | confirmed | active | [requirement.md](./requirement.md) |
| E-011 | 2026-08-29 | 删除版本错误是否足以安全实现需求 | 代码调用链 | `conversation.py`、filesystem scope、`command_runtime/skill_snapshot.py` | 不足。正文只冻结字符串，文件读取授权仍指向活包，命令又在首次执行时另复制；放开正文后会产生正文、引用和脚本混版。修复 owner 必须覆盖统一包 binding，而不是只改 `activate_from_snapshot()`。 | confirmed | archived | [design.md](./design.md) |
| E-012 | 2026-08-29 | 成熟 Agent 的实现能否直接提供完整包冻结方案 | 外部资料/源码 | Codex 固定源码、OpenCode Skill tool、Claude Code Skill lifecycle | Codex 在调用时读取当前正文并以 watcher 清 cache；OpenCode 返回当前正文和活 base directory；Claude Code把已渲染正文留在上下文，但文档没有承诺包内资源同版本。它们支持“激活时加载、已加载内容稳定”的方向，但不能替代 agent-friend 自身完整包快照设计。 | confirmed | archived | [Codex source](https://github.com/openai/codex/blob/430d26b543b219049192de559987b8cf506efacf/codex-rs/ext/skills/src/host_outcome.rs)、[OpenCode source](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/skill.ts)、[Claude Code docs](https://code.claude.com/docs/en/skills) |
| E-013 | 2026-08-29 | 新安装 Skill 同轮使用是否可照搬 Codex | 当前 Codex builtin Skill / 对照 | 本机 Codex `skill-installer` 第 30 行；agent-friend installer 输出 | Codex 当前 installer 仍指示“下一轮可用”。agent-friend 若要同轮使用，必须新增结构化安装结果、宿主验证和轮内候选扩展，不能只依赖 watcher 或自然语言 stdout。 | confirmed | archived | 当前本机 Codex builtin `skill-installer`；[design.md](./design.md) |
| E-014 | 2026-08-29 | 真实 bad case 的模型来源能否确认 | 本地真实会话 | session metadata 与相关 run meta | 事故模型为 `deepseek/deepseek-v4-flash-vision-exp`，reasoning preset 为 `off`。该来源只用于未来若重启评测时选择原事故模型；本次未创建或运行真实模型评测。 | confirmed | retained | 原始 session 仅保留在用户本机，不归档会话 ID 或完整内容 |
| E-015 | 2026-08-29 | 当前需求是否继续实施 | 用户决定 | 技术设计 declare 后的明确请求 | 用户认为需求过重，要求封存整体调研和设计讨论，并把档案合入主干；不创建 `progress.md`，不进入实现、测试或验收。 | confirmed | final | [requirement.md](./requirement.md)、[design.md](./design.md) |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 首次激活完整复制的性能与预算 | 现有 command snapshot 上限足够 / 大型资源 Skill 会产生不可接受的轮级 I/O | 未来重启时先做包规模 inventory 与激活 benchmark，再确定是否需要内容寻址复用 | 需求已封存；不再阻塞当前交付 |
| 新安装显式-only Skill 的同轮调用 | 原用户输入可被宿主精确重解析 / “这个 Skill”类指代仍需要模型语义且不能安全绕过策略 | 用真实显式语法、结构化选择和指代 case 验证候选协议；必要时拆成独立需求 | 需求已封存；候选方案未确认 |
| 三种完整包 digest 是否统一 | installer、first-party updater、command snapshot 可共享一个算法 / 各自身份目的不同，应保持独立并做映射 | 盘点持久化兼容、冻结 runtime 和脚本独立运行约束 | 需求已封存；未来重启时决定 |
| 是否可以只做“已知 Skill 更新后同轮激活” | 能形成明显更轻的窄修复 / 仍无法避免文件和命令混版 | 单独设计只读正文语义或限制同轮后续资源能力，评估是否值得牺牲一致性 | 未来最优先的降级方向 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 将冻结点从轮开始移动到首次成功激活 | E-001～E-003、E-005～E-008、E-010～E-012 | 封存候选 | 未来重启并重新确认需求；不得从本档案直接实施 |
| 首次激活绑定完整 Skill 包而非只绑定 `SKILL.md` | E-004、E-011～E-012 | 封存候选 | 未来 spike 证明更轻方案也能保证正文、文件和命令不混版 |
| 新安装 Skill 使用结构化安装结果扩展本轮候选集 | E-009～E-010、E-013 | 封存候选 | 未来另有宿主可信身份机制，或新安装同轮使用被拆出范围 |
| 激活、权限和副作用治理保持分层 | E-005、E-007～E-010 | 保留原则 | 任何未来方案仍不得因本档案绕过现有治理 |
| 当前产品继续遵循 076、088 的下一用户轮生效语义 | E-003、E-015 | 当前 | 新需求经重新确认、实现和验收后才可覆盖 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001 | 用户截图与本机 agent-friend session JSONL | 不归档原件；本索引仅保存脱敏顺序和错误码 | 原始事件已按 update、skill_read、error、下一轮成功交叉核对 | 含用户会话与本机信息，仅限用户本机，不提交仓库 |
| E-002～E-004 | product HEAD `7def69b819b83b709a8e0313a76db370a4ee7aef` 与需求 076/088 | Git 历史和本索引相对链接 | commit 固定；设计阶段若基线变化需重新对账 | 私有源码与项目文档，不含凭据 |
| E-005 | Agent Skills 开放规范 | 稳定公开 URL | 采集日期已记录；规范页面可能演进 | 公开 |
| E-006 | OpenAI 官方文档与公开源码 | 文档 URL + 固定 commit URL | 源码 commit 固定；文档页面会演进 | 公开；不从产品 UI 反推闭源 Desktop 实现 |
| E-007～E-009 | Anthropic、OpenCode、GitHub 官方文档 | 稳定公开 URL | 采集日期已记录；设计结论需由 agent-friend 自身代码验证 | 公开 |
| E-010 | 当前用户 review | requirement 的范围、AC 与本索引决定摘要 | 用户明确回复“都同意” | 不保存无关对话或用户私密内容 |
| E-011～E-013 | agent-friend 当前代码、Codex/OpenCode 公开源码和当前 Codex builtin Skill | `design.md`、固定公开 URL 与本索引摘要 | 产品代码由 `7def69b...` 锚定；Codex 源码固定 commit；本机 builtin 只保留行级结论 | 私有源码和本机产品文件不复制进仓库；公开源码可复核 |
| E-014 | 本机真实 session metadata | 不归档原始 session；仅保留模型 ID 与 reasoning preset | metadata 与相关 run 一致 | 不保存 session ID、完整消息、prompt、usage 或工具输出 |
| E-015 | 当前用户封存决定 | requirement/design 状态和本索引摘要 | 用户明确要求“封存、落调研与设计讨论、合入主干” | 不保存无关对话 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-28 | 用户截图、session 事件、需求 076/088、当前 Skill 激活与命令快照代码 | 确认问题是旧设计下的确定性行为；仅改错误提示不足以闭环，需新需求调整冻结时机和完整包绑定 |
| 2026-08-28 | Agent Skills、Codex、Claude Code、OpenCode、GitHub Copilot 官方资料与 Codex 固定源码 | 收敛为“元数据发现、首次激活加载、激活后稳定、权限独立”的共同边界；未把外部产品的具体内部结构直接写进需求方案 |
| 2026-08-28 | 用户对 requirement declare 的整体确认 | 纳入可信新安装 Skill 的同轮显式激活、用户轮级完整包绑定和治理边界；需求状态置为 `CONFIRMED` |
| 2026-08-29 | 当前 Skill service、Conversation、filesystem scope、command snapshot、installer 输出与 ToolResult 持久化链 | 确认改动不能缩减为删除 `skill_changed_during_turn`；形成轮级完整包 binding 与结构化 installer effect 的候选设计，并记录未决性能、策略和 digest 问题 |
| 2026-08-29 | Codex 固定源码、当前 builtin installer、OpenCode tool 源码、Claude Code lifecycle 与真实 session 模型元数据 | 外部实现支持渐进加载和已加载内容稳定，但没有可直接复制的 agent-friend 完整包冻结；事故模型来源确认，未运行真实评测 |
| 2026-08-29 | 用户封存决定、requirement、封存候选 design 与 evidence ledger | 需求和候选方案状态统一改为 `ARCHIVED`；不创建 progress、不实施、不测试，076/088 恢复为唯一现行契约 |
