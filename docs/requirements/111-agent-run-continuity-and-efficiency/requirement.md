# 111 · Agent 运行连续性与执行效率

## 状态

CONFIRMED

## 背景

agent-friend 已分别建立 Agent run 统一恢复与终止语义、活动 run 状态投影、受治理的本地命令执行、工具结果上下文保留与动态上下文缓存连续性。这些能力已能处理同一进程内的工具续轮、可修正失败、上下文投影和 cache epoch 分界，但对桌面 Shell/WebView 重启、Bridge 丢失、孤儿工具事务和可恢复外部进程之间的边界仍不完整。

2026-08-30 的一份真实诊断包展示了复合问题：本地 01:36:50 一次长时间命令已持久化 `tool_call_request` 并进入 `tool_batch_start`，但电脑卡死重启前没有产生 tool result 或 run terminal；重启后运行时正确识别为 `cross_process_resume / restored_without_prefix`，但持久化业务 Session 中仍留有未闭合工具事务。同一会话中本地 02:28 的另一次 Bridge 重启由用户主动切换默认对话模型触发，它同样应当建立新 cache epoch，但不应被解释成无原因的运行时崩溃。

该会话还暴露了三类相邻运行问题：模型跨用户轮复用已失效的 Skill/filesystem scope；managed 与 host 命令、Windows 路径、Shell 控制符和语义终态存在较高的试错成本；长会话中重复 Skill 内容、工具结果与大量模型续轮持续推高输入成本。这些问题共同作用于 Agent run 的工具事务、模型可见历史与宿主执行边界，需要在一个需求中统一约束，避免恢复、缓存和权限各自局部正确却组合成错误行为。

## 目标

- 区分“只是前端重挂接”、“Bridge/电脑真实丢失”和“模型或 Provider 配置变化”三类恢复语义，只在运行时事实完整存活时声称 exact continuation。
- Shell/WebView 重启但 Bridge、SessionManager、SessionModelHistory 和任务存活时，让前端重新挂接原 run，任务继续且下一次 provider 请求严格扩展重启前 final wire。
- Bridge 或电脑真实丢失时，恢复持久化业务 Session，明确降级为新 cache epoch，并对已能确认无法继续的孤儿工具事务追加可信的 interrupted 终态。
- 先根据可恢复证据尝试重新挂接，再宣告中断；短暂失联、Bridge generation 变化或结果暂时不可见不得单独成为 interrupted 证据。
- Skill activation 和 filesystem scope 通过 append-only 失效 delta 表达生命周期；旧 item 保留原位，真实权限仍由宿主按当前状态强制校验。
- 收敛模型可见的本地命令能力面，明确分开进程事实与领域语义结果，减少 managed/host、路径、环境变量和长任务中的无效试错。
- 在不伪造前缀连续性、不改写已有事件和不弱化模型可见事实的前提下，治理本地可控的上下文增长与模型续轮成本。

## 范围

### 包含

#### A 类：Shell/WebView-only restart

- 当桌面 Shell 或 WebView 重启，但 Bridge、SessionManager、SessionModelHistory、active run 和它们所属的工具任务仍真实存活时，这次变化只是投影与控制面重挂接，不是新 run 或新 cache epoch。
- 前端从 Bridge 的权威 run、item、tool transaction、approval 和 command process 事实恢复当前状态；不得根据前端本地缓存、最后一条消息或短暂缺失的 push 推断任务已结束。
- 重新挂接不生成伪 completed、failed 或 interrupted，不重放已执行副作用，不创建第二个 run，也不丢弃正在运行的工具输出通道。
- 重挂接后的下一次 provider 请求必须以重启前最后一次 final wire 为严格前缀，只在尾部追加真实发生的 assistant、tool result、context delta 或新 user item。
- 需求 107 的 active run 投影、操作能力、通用活动反馈和 terminal 收口继续成立，本需求只扩展其对 Shell/WebView 重挂接时仍在 Bridge 中存活 run 的恢复边界。

#### B 类：Bridge 或电脑真实丢失

- 当 Bridge、SessionManager 或 SessionModelHistory 已丢失，或电脑重启导致原运行时不再存活时，必须明确建立 degraded/new cache epoch；不根据业务 Session 相似、cache key、模型相同或进程编号重用伪造 strict continuation。
- 恢复以持久化业务 Session 为真相源。旧 user、assistant、tool request、tool result 和其它业务事件保留原位，不删除、移动、替换或回写。
- 只有当宿主取得足够证据确认某个未闭合工具事务已不可能继续时，才追加宿主生成的 interrupted 终态；该终态保留原 run 和 tool call 关联，且重复恢复不会产生第二个终态。
- 对仍持有可验证运行身份、所有权和重挂接能力的工具或宿主进程，应先尝试重新挂接、查询已完成结果或恢复输出通道；不得因 Bridge generation 变化或短暂不可达就误写 interrupted。
- 新 epoch 可以把新追加的 interrupted 事实作为业务历史输入模型，但不为跨进程精确续接而新增 prompt、Memory recall、Skill 正文、工具正文或其它 model-only transcript 持久化。
- 不能判定“仍可恢复”或“已确定失联”时，恢复状态必须保留为待对账/未知，不为了快速收口伪造任一终态。

#### C 类：模型、Provider 或默认配置变化

- 会改变当前模型、Provider route、默认对话模型、request settings 或它们所属 Bridge 运行时的用户动作，按需求 110 的已确认语义建立新 cache epoch。
- 即使配置变更前后的业务消息具有公共前缀，也不将该请求报告为普通 strict continuation，不用局部 provider cache read 反推宿主连续性。
- 配置变更引起 Bridge 重启时，正在运行的工具或宿主进程仍按 B 类可恢复性边界对账；“建立新 cache epoch”不等于“所有任务都已失败”。
- 诊断与观测应区分用户配置触发、正常服务重建、强制终止、无原因退出和计算机重启，不把所有 process generation 变化合并成单一“崩溃”类别。

#### 可恢复性判定与事务闭合

- 每类长任务、命令、授权或外部进程必须有明确的恢复能力声明：支持重新挂接、只支持查询最终结果、无法恢复，或当前证据不足。
- 可恢复性裁决使用宿主信任的运行身份、所有权、存活性、进程或 operation 状态和完成证据；模型自报、旧历史文案或固定等待时间不能单独决定。
- 恢复过程有可查询的状态，能区分正在重新挂接、已恢复、已确认中断和证据不足；不将中间对账态投影成 completed 或 failed。
- 宿主生成的 interrupted 事实必须幂等，与已完成的 tool result、run terminal 或后续重挂接结果不产生相互矛盾的双终态。
- 恢复后的业务 Session、Runtime、实时 push、桌面投影、历史重放和诊断对同一事务使用一致的恢复或中断事实。

#### Skill activation 与 filesystem scope 失效 delta

- 已经进入当前 cache epoch final wire 的 Skill 指令、激活结果和 filesystem scope item 保留在原位，不因用户轮结束、scope 过期或新 scope 产生而遮蔽、删除、移动或替换。
- Skill 当前未激活、scope 已失效、权限变化或同一能力被新 scope 取代时，通过尾部 append-only delta 表达最新事实和适用边界。
- 宿主每次执行文件或 Skill-bound 操作时仍以当前权威 ledger 校验；历史 scope 即使对模型可见也不构成授权，被拒绝时返回稳定、可行动且不泄漏新能力的错误。
- 新的有效 Skill 激活或 filesystem scope 只在当前任务需要时按需追加，不在每个 provider 续轮重复插入语义相同的正文或状态。
- 本需求只治理已有 Skill/scope 的模型可见生命周期，不恢复需求 108 封存的“同轮安装或更新 Skill 后热切换完整包版本”范围。

#### 本地命令契约与双层结果语义

- 模型可见的命令契约应明确区分受管执行环境、显式宿主程序和不可用路线，不让模型用同一字段同时猜测 managed/host 身份、shell 和目标进程。
- 结构化参数能表达的可执行文件、argv、环境变量、工作目录、文件范围、网络和超时不要再要求模型拼接不可验证的 shell 文本；确实需要 shell 语义时使用明确的独立路径和错误边界。
- 路径存在性、空格与引号、additional root、Skill binding、当前激活状态和受管命令的 direct invocation 要求在副作用发生前校验，并返回指向精确字段或前置条件的可修正错误。
- 通用命令运行时返回可区分的 `process_status`，如未启动、运行中、正常退出、非零退出、被取消、超时、失联或结果未知；这些是进程事实，不自动等同于业务成功或失败。
- 只有对领域结果拥有判定权的工具、wrapper 或结构化协议可以返回 `semantic_status`，并区分成功、失败、完成但有警告、未评估或结果未知；通用 runtime 不从 stdout、stderr 或 exit code 自行推断。
- 长时间命令应提供可查询的 operation/process 身份、进度、取消和重挂接能力说明；不支持重挂接时必须事先明确，不在丢失后假设任务仍在运行。
- 现有命令安全、审批、文件范围、网络、超时、取消和反重放边界继续成立；减少试错不等于宿主替模型选择越权路线。

#### 上下文成本治理

- 只治理 agent-friend 本地可控的输入增长、工具结果形状、重复上下文和无必要续轮，不把 provider 的 cold/warm、TTL、路由或内部缓存政策写成本地可修复根因。
- 相同 cache epoch 中已进入 final wire 的 item 不为降低输入成本而静默删除、移动、替换或遮蔽；需要改写历史时通过明确 compaction/new epoch 表达。
- 语义相同且状态未变的 Skill 正文、capability snapshot、scope 状态和其它动态 contributor 不在每个 provider 续轮重复追加；变化时只追加最小充分 delta。
- 模型可见的工具结果优先返回完成下一步所需的高信号结构，大型原始输出通过现有恢复机制按需查询；缩减不得丢失终态、标识符、副作用、截断和恢复边界。
- 能由确定性宿主完成的预检、身份绑定、结果分类和恢复对账不要额外消耗模型续轮；仍需模型做新判断的结果继续返回模型。
- 继续记录 prompt/input/cache read/cache miss、payload 大小、工具结果大小、模型调用数、续轮数和端到端时间；评估同时报告缓存前缀事实与总输入变化。

#### 可观测性、证据与回归

- 运行时和诊断能够区分 Shell/WebView-only restart、Bridge 丢失、计算机重启、模型/Provider/default 配置变化、正常服务重建、强制终止和未知原因。
- 每次 reattach、degraded/new epoch、orphan reconciliation、interrupted 追加和命令语义裁决保留稳定 reason code、原 run/tool identity、证据类型和幂等结果，但不记录凭据、不必要的命令正文或 model-only transcript。
- 确定性测试覆盖 A/B/C 三类恢复、可恢复性矩阵、重复启动幂等、迟到结果、新旧 run 隔离、scope 失效 delta、命令双层状态和上下文成本不变量。
- 本次诊断包的运行时事件与缓存分段统计作为 baseline；后续 candidate 必须使用同口径指标并明确代码、模型、路由、运行状态和 cold/warm 差异。
- 真实 LLM 或 provider cache 复验只用于观测外部行为，执行前继续遵守真实 LLM 调用授权；不用单次真实运行替代确定性恢复契约测试。

### 不包含

- 不修改需求 110 已确认的隐私边界；不为跨进程精确延续而持久化 prompt、Memory recall、Skill 正文、工具正文或其它 model-only transcript。
- 不要求计算机重启、Bridge 丢失、模型切换、Provider route 变化或 request settings 变化后仍声称 strict continuation。
- 不保证 DeepSeek 或其它 Provider 的缓存命中率，不把未定位的同进程低命中归因于本需求可修复的本地缺陷，也不取代独立 Provider 调查。
- 不恢复需求 108 封存的同轮 Skill 安装/更新、完整包快照和热切换范围。
- 不要求桌面 Shell 重启时必然保活 Bridge；A 类验收只适用于 Bridge 与原 run 运行事实确已存活的场景。
- 不为无恢复协议的任意外部进程伪造可重挂接能力，不保证所有第三方命令在 Bridge 丢失后继续受管。
- 不新增、修改或评价 MAA 或其它具体 Skill 的业务配置、流程和领域结果。
- 不放宽文件、命令、网络、审批、超时、取消、路径和副作用治理边界。
- 不重新设计整个桌面工具卡片、任务中心或运行历史界面；只要求现有表面能准确消费恢复与终态事实。
- 不因本需求自动执行真实 LLM 调用、高成本压测或外部写入。

## 关键信息

- exact continuation 是运行时可证明的 transcript 关系，不是 cache key 相同、provider 返回了少量 cache read 或用户仍在同一个业务 Session 的同义词。
- cache epoch 分界与任务是否可恢复是两个问题：新 epoch 中仍可能重新挂接外部任务；任务已中断也不允许伪造旧 epoch 的前缀连续。
- persisted business Session 是跨进程恢复的审计真相源；SessionModelHistory 是进程内 provider-facing 精确顺序状态，它丢失后不通过新增敏感持久化伪造完整恢复。
- tool request、tool result、宿主生成的 interrupted 和 run terminal 都是 append-only 事实。恢复可以追加更新事实，不能改写已发生的原始事件。
- “未知”是正式恢复状态，不是必须被快速归并到 failed 或 interrupted 的临时错误。
- 历史 Skill/scope item 对模型可见不等于当前有效；失效 delta 负责更新模型可见事实，宿主 ledger 负责权威拒绝。
- `process_status` 只回答进程发生了什么；`semantic_status` 回答有权判定的领域结果是什么。后者缺失时必须保留为未评估或未知，不由模型或通用 runtime 凭日志猜测。
- 前缀连续性、provider cache read、总输入成本和端到端时间需要分开报告；任一单项改善都不能自动证明其它指标的根因已解决。

## 验收条件

### A 类：Shell/WebView-only restart

- [ ] **AC-A1 重挂接不中断**：Bridge、SessionManager、SessionModelHistory、active run 和工具任务均存活时，重启 Shell/WebView 后前端重新挂接原 run，任务继续，不产生 completed、failed、cancelled 或 interrupted 伪终态。
- [ ] **AC-A2 无重复副作用**：重挂接不创建第二个 run，不重放已执行工具，原 tool call、process/operation 身份和输出通道继续受权威运行状态管理。
- [ ] **AC-A3 严格前缀**：重挂接后下一次 provider final wire 以重启前最后一次 final wire 为字节/项顺序语义上的严格前缀，只追加重启后新事实；observation 报告 exact continuation 且不建立新 epoch。
- [ ] **AC-A4 投影对账**：会话切回、页面刷新、push 重连和 REST 对账后，active run、主要活动反馈、授权、命令和工具事务收敛到 Bridge 中同一权威事实。

### B 类：Bridge/电脑丢失

- [ ] **AC-B1 明确新 epoch**：Bridge、SessionManager 或 SessionModelHistory 丢失，或电脑重启后，恢复请求标记 degraded/new epoch，不报告 strict continuation，不根据少量 cache read 伪造连续。
- [ ] **AC-B2 孤儿事务闭合**：对已持久化 tool request 但无 result/run terminal，且已取得决定性失联证据的事务，追加与原 run/tool call 关联的宿主 interrupted 事实并收口 run；原事件序列不改写。
- [ ] **AC-B3 优先重挂接**：当工具或外部进程具有可验证身份、所有权和恢复协议时，恢复流程先重新挂接或查询结果，不误写 interrupted；迟到 result 不与已存终态形成双终态。
- [ ] **AC-B4 未知态保留**：证据不足时事务保留在可查询的待对账/未知态，不只因等待超时或 generation 变化就归类为已恢复或已中断。
- [ ] **AC-B5 幂等恢复**：应用多次启动、多个前端重连或恢复扫描重试只能产生一个有效恢复或中断结果，不重复执行副作用或重复追加终态。
- [ ] **AC-B6 隐私边界**：检查 Session、数据库、日志、diagnostics 和新增恢复状态，跨进程恢复没有新增 prompt、Memory recall、Skill 正文、工具正文或其它 model-only transcript 持久化。

### C 类：模型/Provider/default 配置变化

- [ ] **AC-C1 配置变化建立新 epoch**：切换模型、Provider route、默认对话模型或 request settings 后，后续请求明确建立新 epoch，不报告普通 strict continuation。
- [ ] **AC-C2 原因可区分**：配置变更导致的 Bridge 重启、正常服务重建、强制终止、未知退出和计算机重启具有不同的稳定 reason code 和诊断分类。
- [ ] **AC-C3 任务恢复与 epoch 解耦**：配置变更引起 Bridge 重启时，新 epoch 事实不会自动把可重挂接外部任务判为失败；任务使用 B 类证据边界独立对账。

### Skill/scope、命令与成本治理

- [ ] **AC-D1 Skill/scope append-only 失效**：同一 cache epoch 内，旧 Skill activation 和 filesystem scope item 保留原位，失效或替换只在尾部追加 delta；新有效 scope 按需追加，不改写旧 final wire。
- [ ] **AC-D2 宿主权限真相**：模型调用历史 scope 时宿主确定性拒绝且不执行副作用；错误指明失效事实和安全恢复方式，但不泄漏未授权新 scope。
- [ ] **AC-D3 命令能力面**：确定性测试覆盖 managed/host 路由、结构化 argv/env/workdir、Windows 空格路径、additional root、Skill binding 和 direct-invocation 预检；已知非法状态在执行或授权前被拒绝。
- [ ] **AC-D4 双层命令状态**：命令结果分开 `process_status` 与 `semantic_status`；非零退出不自动证明语义失败，零退出不自动证明语义成功，没有语义裁决者时明确返回未评估/未知。
- [ ] **AC-D5 长任务恢复能力**：长命令返回稳定 operation/process 身份与可恢复性声明；支持的任务可重新挂接、查询进度、取消和取得终态，不支持的任务不伪装成可恢复。
- [ ] **AC-D6 前缀与成本不变量**：无状态变化时不重复追加相同 Skill/capability/scope 内容；需要改写历史的工具结果清理或 compaction 建立新 epoch，不报告为 strict extension。
- [ ] **AC-D7 高信号结果**：工具默认模型可见结果仅保留终态、标识符、副作用、错误、截断和下一步所需信息；大型原始结果仍可按权限与恢复协议查询。
- [ ] **AC-D8 指标对照**：使用本需求 evidence index 定义的 baseline/candidate 口径，同时报告 provider 请求数、模型续轮数、prompt/input/cache read/cache miss、payload 大小、工具结果体积和端到端时间；不把缓存命中变化单独归因于 111。
- [ ] **AC-D9 自动化回归**：确定性测试覆盖恢复状态矩阵、孤儿事务、迟到结果、双终态防护、跨 run 隔离、scope 失效、命令状态和上下文不变量；需求 075、095、107、109、110 的已确认语义不回归。
- [ ] **AC-D10 项目门禁**：相关 lint、typecheck、单元测试、集成测试和 `scripts/check` 全部通过；真实 LLM/provider 复验只在本次取得明确授权后执行并保留观测局限。

## 开放问题

- 可恢复性裁决需要组合哪些宿主信任事实，以及证据不足时如何从待对账状态收敛，由技术方案根据现有 runtime lease、process/operation 身份、心跳与结果存储边界确定。
- 宿主 interrupted 采用何种持久化事件形状，它与 tool result、run terminal、迟到 result 和 UI 投影的先后/幂等关系，由技术方案在不改写旧事件的前提下确定。
- Bridge 丢失后外部进程仍存活但原 stdout/stderr 通道已断时，哪些工具可重新挂接、只能查询最终结果或必须保留未知，需在方案阶段建立逐类矩阵。
- `semantic_status` 的权威提供者、字段集合和与现有 `is_error`/exit code/UI 的兼容投影，需根据通用命令 runtime 与领域 wrapper 的责任边界设计。
- 上下文成本 candidate 的具体收益阈值不在需求阶段预设；技术方案应优先保证前缀、终态、权限和语义完整性，再结合 baseline 选择可测量的幅度目标。

## 关联上下文

- [063 · 上下文压缩可靠性](../063-context-compaction-reliability/requirement.md)
- [075 · 工具结果上下文保留策略](../075-tool-result-context-retention/requirement.md)
- [078 · 受治理的通用本地文件工具](../078-governed-local-filesystem-tools/requirement.md)
- [082 · 工具执行治理](../082-tool-execution-governance/requirement.md)
- [085 · 受治理的通用本地命令运行时](../085-governed-local-command-runtime/requirement.md)
- [095 · Agent 运行恢复与终止语义](../095-resilient-agent-run-recovery/requirement.md)
- [096 · 受管本地执行环境](../096-managed-local-execution-environment/requirement.md)
- [101 · 会话推理强度控制](../101-session-reasoning-effort-control/requirement.md)
- [102 · Provider transcript 前缀复用](../102-provider-transcript-cache-reuse/requirement.md)
- [104 · Agent 主动终止命令](../104-agent-controlled-command-termination/requirement.md)
- [107 · 活动运行状态与界面投影一致性](../107-active-run-state-consistency/requirement.md)
- [108 · Agent 可控的 Skill 激活生命周期（已封存）](../108-agent-controlled-skill-activation-lifecycle/requirement.md)
- [109 · Prompt cache 可观测性与优化基线](../109-prompt-cache-observability-and-optimization/requirement.md)
- [110 · 动态上下文缓存连续性](../110-dynamic-context-cache-continuity/requirement.md)
- [证据索引](./evidence-index.md)

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-30 | 首次确认：以 A/B/C 三类恢复语义统一 Shell/WebView 重挂接、Bridge/电脑丢失、配置变更新 epoch、孤儿工具事务闭合与可恢复性边界；同时纳入 Skill/scope 失效 delta、命令双层状态和本地上下文成本治理 | 新需求 |
