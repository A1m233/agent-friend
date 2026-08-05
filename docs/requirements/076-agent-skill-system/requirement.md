# Agent Skill 系统

## 状态

CONFIRMED

## 背景

agent-friend 当前通过固定 system prompt、工具说明和代码逻辑提供 Agent 能力，但缺少一套可由用户扩展、按任务需要加载、可跨兼容 Agent 客户端共享的 Skill 机制。重复工作流只能继续固化进产品代码或长期 prompt，既不利于复用，也会让所有说明长期占用上下文。

[Agent Skills 开放规范](https://agentskills.io/specification) 已定义以 `SKILL.md` 为入口、配合 `references/`、`scripts/` 和 `assets/` 的可移植目录格式，但规范并未替具体 Agent 决定发现目录、触发方式、上下文生命周期和运行时能力边界。agent-friend 需要在兼容该格式的基础上，形成适合持久化长会话的产品行为契约。

用户还可能在 agent-friend 运行期间新增、修改或删除个人 Skill。当前会话必须能够自动感知 catalog 变化，不能要求用户重启应用、重启 Agent 或重新创建会话。

除用户提供的 Skill 外，agent-friend 还需要能够随产品交付 builtin Skill。首个 builtin Skill 为 Codex 官方开源的 `skill-creator`，用于向 Agent 提供统一的 Skill 创建与维护指引；它仍然只能使用 Agent 已经拥有的公共工具，不能获得一套绕开统一权限模型的私有文件或命令能力。

## 目标

- 让用户能够通过标准 Skill 目录扩展 Agent 的可复用任务工作流。
- 让产品能够以同一格式交付 builtin Skill，首期包含与 Codex 官方版本对齐的 `skill-creator`。
- 同时支持 agent-friend 专属个人 Skill 与可同其他兼容 Agent 共享的个人 Skill。
- 通过渐进式披露控制上下文：常态只提供有界 catalog，调用方结构化选中后才加载完整 `SKILL.md`；其它资源保留按需读取边界，但是否能实际读取取决于 Agent 已有的公共本地文件能力。
- 对齐 Codex 的上下文语义：Skill 正文按发生顺序进入会话历史，逻辑适用范围由当前用户轮决定，不在轮次结束时回写或重组既有历史。
- 在当前会话中自动刷新 Skill catalog，使运行期间发生的文件变化能够用于后续用户轮。
- 让桌面端和普通 OpenAI-compatible 对话入口使用同一份 Skill catalog、选择和上下文装配语义。
- 只接受调用方提供的精确结构化 Skill 选择，不通过正则、`$name` 语法或用户 query 语义分析来猜测本轮要加载哪个 Skill。
- 如实反映 agent-friend 当前运行时能力，不因 Skill 要求修改文件或包含脚本而假装已经完成操作，也不为某个 Skill 增加私有特权工具。

## 范围

### 包含

#### Skill 格式与校验

- 识别以 `SKILL.md` 为入口的标准 Skill 目录。
- 支持规范要求的 `name`、`description` 元数据，并允许 Skill 携带规范定义的可选元数据。
- 允许 Skill 目录包含 `references/`、`scripts/`、`assets/` 及其它由 `SKILL.md` 引用的同包资源。
- builtin、agent-friend 专属个人 Skill 和共享个人 Skill 使用同一套目录格式、校验规则与渐进式加载机制。
- 单个 Skill 无效、不可读或不兼容时，不阻止其它有效 Skill 被发现和使用；系统提供可诊断的失败信息。
- Skill 的来源、入口路径和当前内容版本能够被区分，避免把不同来源或不同版本误当成同一份指令。

#### Skill 来源、身份与 builtin

- 支持随 agent-friend 交付并默认可发现的 builtin Skill 来源。
- 首期 builtin Skill 包含 `skill-creator`，其内容和资源基于 Codex 官方开源版本，并保留可追溯的上游来源、许可证与必要声明。
- builtin Skill 不拥有隐式额外权限；它与其它 Skill 一样，只能驱动 Agent 使用已经注册并获准调用的公共工具。
- 支持 agent-friend 专属个人 Skill 目录。该目录属于 agent-friend 用户数据，不要求其它 Agent 客户端识别。
- 支持用户主目录下的共享个人 Skill 根 `~/.agents/skills`，使用户可以主动选择与 Codex 等兼容客户端共享 Skill。
- 首期不扫描当前工作目录或其祖先目录中的项目级 `.agents/skills`；agent-friend 的普通会话不以代码仓库或 workspace 作为稳定作用域。
- 不直接扫描其它客户端的专属目录，例如 `~/.codex/skills` 或 `~/.claude/skills`。
- Skill 根或单个 Skill 允许通过当前平台支持的符号链接或目录链接共享；系统以规范化后的实际入口识别重复，并对失效、越界循环或不可读链接提供诊断。
- 不同来源或不同实际路径中的同名 Skill 不合并、也不按名称静默覆盖。每个候选保留来源和稳定身份；相同规范化入口只保留一份，调用方必须通过结构化选择精确指定目标。

#### Catalog 与渐进式加载

- Agent 在常态上下文中获得可用 Skill 的有界 catalog，至少包含名称、描述、来源和精确选择所需的稳定身份。
- 桌面端与普通 OpenAI-compatible 对话入口都通过共享的会话装配路径获得同口径 catalog；不能只在某一个前端入口中临时拼接 Skill。
- catalog 受明确的上下文预算约束；Skill 数量过多时不得无界扩张 prompt，并应让被截断或省略的事实可诊断。
- 本期只有调用方在当前用户轮中提供精确、结构化的 Skill 选择时，系统才激活对应 Skill。后续前端可以提供确定的选择交互，但专用管理或选择 UI 不属于本期交付。
- 后端不从原始用户文本中用正则解析 `$skill-name`、自然语言点名或其它隐式语法，也不根据 query 与 description 的语义相似度自动选择 Skill。
- Skill 未被激活前，不把其完整 `SKILL.md` 或其它包内资源全部放入模型上下文。
- Skill 激活时加载完整 `SKILL.md`；不预加载 `references/`、`assets/` 等附加资源。未来具备公共本地文件读取能力后，只在 `SKILL.md` 指引或当前任务确实需要时继续读取。
- 本期不为附加资源增加 Skill 专用读取旁路。当前任务依赖尚不可用的本地附加资源时，Agent 明确报告公共文件读取能力缺口，而不是假装已经读取或把整个 Skill 包提前塞入上下文。
- 如果结构化选择指向的 Skill 不存在、已禁用、无效或无法读取，Agent 明确说明，并在可能时继续采用不依赖该 Skill 的合理方案。

#### Skill 的上下文语义

- catalog 作为稳定的 Agent 上下文存在；catalog 没有变化时，不在每个用户轮重复追加相同 catalog。
- Skill 正文首次加载后，作为有明确名称、来源和边界的上下文项追加在触发它的用户输入之后，并进入会话历史。
- Skill 激活不得为了把正文插入更早位置而重组 system prompt、改写既有消息或破坏已经形成的会话前缀。
- 完整 Skill 正文在用户轮结束后继续作为历史上下文存在，不因逻辑作用域结束而被主动删除或替换成摘要标记。
- Skill 是否适用于当前任务由当前用户轮携带的结构化选择决定；之前使用过某个 Skill，不表示它自动成为后续所有任务的常驻规则。
- 调用方后续再次结构化选择同一 Skill 时，系统按当前文件内容重新激活该 Skill。
- Skill 上下文服从既有会话历史、上下文投影和压缩边界，不被提升为永久不可压缩的 system 指令；压缩后再次激活时重新取得当前文件内容。

#### Catalog 热更新

- 在应用和会话保持运行时，自动监测两个个人 Skill 来源中的新增、修改、删除和重命名。
- 文件变化稳定并完成 catalog 刷新后，新建 Skill 可被发现，元数据修改可被看到，已删除 Skill 不再出现在后续 catalog 中。
- 热更新不要求用户重启应用、重启 Agent、重新创建会话或手动执行刷新命令。
- 已经开始执行的用户轮使用该轮开始时确定的 Skill snapshot，不因中途文件变化而替换指令；刷新后的 catalog 从后续用户轮生效。
- catalog 更新以新的上下文状态追加到既有会话，不回写已经发送的历史前缀。
- Skill 正文已进入历史后，磁盘上的后续修改不篡改旧历史；下一次激活使用更新后的文件内容。

#### `skill-creator` 的能力边界

- `skill-creator` 作为 builtin Skill 提供 Skill 规划、目录约定、`SKILL.md` 编写、配套资源组织和验证方式等官方工作流指引。
- `skill-creator` 与其它 Skill 共用 catalog、加载、上下文和资源读取机制，不新增 `SkillAuthoringService`、私有 authoring tool 或只能由 builtin 调用的写入旁路。
- 当前 Agent 缺少通用本地文件读写能力时，`skill-creator` 可以基于已加载的 `SKILL.md` 完成分析和内容建议，但不能声称已经读取附加文件、创建目录、写入或编辑文件；该公共能力缺口由 [issue 044](../../issues/044-agent-general-filesystem-mutation-tools/) 跟踪。
- 当前 Agent 缺少通用命令执行能力时，`skill-creator` 不能运行其配套初始化、元数据生成或校验脚本；该公共能力缺口由 [issue 045](../../issues/045-governed-command-and-skill-script-runtime/) 跟踪。

#### 脚本与运行时能力边界

- `scripts/` 只表示 Skill 包可以携带可执行资源；发现 Skill、加载 catalog 或加载 `SKILL.md` 时均不自动运行脚本。
- 首期允许包含 `scripts/` 的 Skill 被发现和使用，且不因 agent-friend 暂无通用命令执行能力而把整个 Skill 判为无效。
- 完整 `SKILL.md` 中的说明可以正常使用；独立 `references/`、`assets/` 等本地资源留待公共本地文件读取能力补齐后按需使用。
- 当完成任务必须执行脚本、但当前 Agent 没有受支持的执行能力时，Agent 明确报告该运行时能力不可用，不得声称脚本已运行或据此伪造结果。
- 后续补齐通用命令能力后，脚本也只在已加载的 Skill 指引和当前任务确实需要时由 Agent 主动调用，不因 catalog 刷新或正文加载自动运行。

#### 诊断与验证

- 诊断能够区分 builtin、专属个人和共享个人来源，以及有效 Skill、无效 Skill、同名冲突、链接异常、catalog 预算省略、热更新和运行时能力不支持。
- 核心发现、校验、来源身份与去重、catalog 更新、激活、上下文装配和错误路径均具备确定性自动化回归覆盖。
- 测试默认使用临时目录和 fake/mock 模型，不因本需求自动调用真实外部 LLM。

### 不包含

- 通用 shell、CMD、PowerShell、终端或任意脚本执行器。
- 通用本地目录枚举和文件读取工具。
- 创建目录、创建或编辑文件、应用补丁、移动或删除文件等通用文件变更工具。
- 只能读取 Skill 包、只能由 `skill-creator` 使用或绕开公共文件权限模型的私有资源读取工具。
- 脚本沙箱、文件与网络权限模型、执行审批、超时、资源配额和审计系统。
- `skill-creator` 私有的 authoring service、文件写入工具或脚本 runner。
- 从原始用户 query 解析 `$name`、自然语言点名或自动语义匹配并激活 Skill。
- 项目级或 workspace 级 Skill 发现，包括从当前工作目录向上扫描 `.agents/skills`。
- Skill 商店、在线市场、远程安装、自动更新、发布和签名分发。
- MCP、云端或其它远程 Skill 来源。
- Skill 管理、选择、编辑、导入、启停和冲突解决的专用桌面 UI。
- 把 Skill 当作永久 Persona、用户偏好、长期记忆或全局不可压缩规则。
- 自动执行 Skill 中的脚本，或根据 Skill 声明临时扩大 Agent 的工具权限。

## 关键信息

### Catalog、激活与历史的口径

- “catalog”指稳定提供给模型、用于说明当前可用 Skill 的有界元数据集合，不包含所有 Skill 正文，也不因自身存在而触发 Skill 激活。
- “激活”指调用方在当前用户轮中通过稳定身份结构化选择某个 Skill，并把其完整 `SKILL.md` 提供给模型；读取附加资源属于后续按需动作。
- “当前用户轮”包含一次用户输入，以及生成该次最终回答前的模型续轮和工具调用。
- “逻辑上不跨轮自动沿用”与“正文仍物理存在于会话历史”是两件事。前者控制当前任务是否应遵循该 Skill，后者保持对话顺序与上下文前缀稳定。

### 热更新口径

- “实时”表示事件驱动地自动感知变化，并在同一运行会话内刷新，而不是承诺修改正在执行中的模型请求。
- 监听方式、去抖窗口、跨平台差异、编辑器原子保存、符号链接、临时非法文件和监听失效后的兜底策略在 `design.md` 中确定。
- catalog 已刷新但 Skill 尚未再次激活时，只更新其可发现元数据，不把全部新正文主动塞入上下文。

### 共享目录的责任边界

- `~/.agents/skills` 是用户主动选择的跨客户端共享面；共享只保证标准目录和文本资源可发现，不保证不同客户端拥有相同工具、脚本执行能力或扩展元数据语义。
- agent-friend 对 Skill 的可用性判断必须以自身实际工具和运行时能力为准。

### 官方参考与公共能力缺口

- Agent Skills 目录规范：[agentskills.io/specification](https://agentskills.io/specification)。
- 首期 builtin `skill-creator` 的官方上游：[openai/codex · skill-creator](https://github.com/openai/codex/tree/main/codex-rs/skills/src/assets/samples/skill-creator)。
- 通用本地文件读写能力由 [issue 044](../../issues/044-agent-general-filesystem-mutation-tools/) 跟踪。
- 受治理的命令与 Skill 脚本运行时由 [issue 045](../../issues/045-governed-command-and-skill-script-runtime/) 跟踪。

## 验收条件

- [ ] **AC-1 三类 Skill 发现**：当前运行实例能够发现随产品交付的 builtin Skill，以及分别放入 agent-friend 专属个人目录和 `~/.agents/skills` 的有效 Skill，并取得其名称、描述、来源、稳定身份和入口路径。
- [ ] **AC-2 builtin `skill-creator`**：默认 catalog 包含来源和许可证可追溯的官方 `skill-creator`；它使用与其它 Skill 相同的加载路径，且没有额外的私有文件或命令工具。
- [ ] **AC-3 无效 Skill 隔离**：缺少必填元数据、内容不可读或格式无效的 Skill 不进入有效 catalog；同一根目录内其它有效 Skill 仍可发现和激活，错误原因可诊断。
- [ ] **AC-4 同名与链接处理**：不同来源或不同实际路径中的同名 Skill 各自保留且不合并、不静默覆盖；指向同一规范化入口的链接只形成一个候选，失效或循环链接可诊断。
- [ ] **AC-5 有界 catalog 与入口一致性**：大量 Skill 存在时，桌面端和普通 OpenAI-compatible 对话入口得到同口径、受预算约束的 model-visible catalog；系统能够说明发生了描述截断或 Skill 省略。
- [ ] **AC-6 结构化激活与渐进式加载**：调用方精确选择有效 Skill 后，模型获得完整 `SKILL.md`，附加资源不预加载；未选择时只有 catalog 元数据，选择不存在、不可读或任务依赖当前不支持的本地附加资源时，Agent 如实说明对应缺口并合理降级。
- [ ] **AC-7 不解析 query**：包含 `$name`、自然语言 Skill 名称或与 description 高度相似的普通用户文本，在没有结构化选择时都不会触发 Skill 正文加载。
- [ ] **AC-8 上下文顺序与跨轮语义**：Skill 正文追加在触发它的用户输入之后并进入历史，不重组既有前缀；后续无关用户轮不会仅因历史中曾出现该正文就自动把 Skill 当作当前规则。
- [ ] **AC-9 历史与再次激活**：用户轮结束后，已注入的 Skill 正文仍按既有历史策略存在；同一 Skill 后续再次被结构化选择时读取当前文件内容，压缩或文件更新后的再次激活不会错误复用已失效版本。
- [ ] **AC-10 新增热更新**：会话保持打开时新增有效 Skill；无需重启或新建会话，catalog 刷新完成后发起的下一用户轮能够发现并结构化选择它。
- [ ] **AC-11 修改与删除热更新**：会话保持打开时修改 Skill 元数据或删除、重命名 Skill；无需重启或新建会话，刷新后的后续用户轮看到更新后的 catalog，旧历史内容不被改写。
- [ ] **AC-12 执行中 snapshot 稳定**：用户轮执行期间修改其 Skill 文件，不改变该轮已经装配的指令；变化仅在后续用户轮重新发现或激活时生效。
- [ ] **AC-13 文件与脚本边界真实**：发现、刷新 catalog 和激活 Skill 不会创建或编辑用户 Skill 文件，不会预加载附加资源，也不会自动执行 `scripts/`；`skill-creator` 或其它 Skill 需要当前不支持的公共本地文件读写或命令能力时，Agent 返回真实能力缺口。
- [ ] **AC-14 既有会话能力回归**：未配置个人 Skill、个人 Skill 根不存在或所有个人 Skill 均无效时，builtin catalog 与普通对话仍可用，Persona、记忆、工具调用、上下文压缩和会话恢复不退化。
- [ ] **AC-15 自动化门禁**：发现、链接、同名冲突、校验、预算、结构化激活、query 不触发、上下文顺序、文件热更新、压缩后再激活和运行时能力边界均有确定性测试，且项目完整检查门禁通过。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-06 | 首次确认：采用专属与共享双个人根、标准渐进式加载、Codex 对齐的上下文语义、当前会话 catalog 热更新，并将任意脚本执行排除在首期范围外。 | 新需求 |
| 2026-08-06 | 回溯确认：新增 builtin 来源与官方 `skill-creator`；激活收紧为调用方结构化精确选择，不解析用户 query；文件变更和命令执行作为公共 Agent 能力分别转入 issue 044、045，不提供 Skill 私有旁路。 | builtin、激活、来源冲突、运行时边界与验收条件 |
| 2026-08-06 | 方案前校正：确认 agent-friend 当前也缺少通用本地文件读取能力；本期只自动加载完整 `SKILL.md`，不为 `references/`、`assets/` 新增 Skill 私有读取旁路，统一由 issue 044 跟踪未来公共文件读写能力。 | 渐进式加载、资源边界、issue 044 与 AC-6/AC-13 |
| 2026-08-06 | 合入最新主干时发现 issue 043 已被占用，将本需求的两个延期问题顺延为 issue 044、045。 | 仅修正文档编号与引用，不改变需求或实现语义。 |
