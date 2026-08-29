# 109 · Prompt cache 可观测性与优化基线

> Prompt Cache Observability and Optimization Baseline

## 状态

CONFIRMED

## 背景

[102 · Provider transcript 前缀复用](../102-provider-transcript-cache-reuse/requirement.md) 已经修复单次 Agent run 内连续模型请求的主要前缀破坏问题，并在受控 DeepSeek 工具续轮中观察到约 `99.56%` 的输入 token 缓存读取比例。该结果证明稳定 provider transcript 能带来显著收益，但不能解释真实产品流量中剩余的未命中来自哪里，也不能外推为跨用户回合、跨 provider 或整体流量的命中水平。

当前仓库已经分别具备 provider usage 归一化、adapter-input 指纹、final-wire request witness、run latency 日志、持久化 cache diagnostics 和真实 prompt-cache smoke。然而这些能力分散在不同层级和输出中，字段口径、生命周期与消费方式尚未形成一份统一契约，工程侧无法稳定回答：

- 当前样本有多少请求真实返回了缓存 usage，多少只是缺少观测；
- 一次请求是首次写入、可复用但结构改变、结构稳定但 provider 未读取，还是实际读取了缓存；
- 不同 provider 返回的总输入、缓存读取、缓存写入和普通输入是否存在包含关系，聚合时是否发生重复计数；
- 未命中输入主要集中在哪个 provider、模型、调用阶段、请求规模、重建原因或交互入口；
- 缓存变化对实际成本、首 token 时延和总时延的影响有多大，哪些只是相关而非已证实的因果关系。

此外，需求 102 的 final-wire witness 当前主要跟随单次 run 的 continuation projection。新用户回合开始后，上一 run 的逐 item witness 被销毁，第一请求通常退回 `baseline`；这使 adapter-input 虽能在同进程中提供方向性比较，最终 provider wire 却无法证明上一用户回合的稳定前缀是否仍被完整保留，也无法量化因 system/persona/tools/history/runtime/settings/model route 重新装配造成的跨回合缓存机会损失。跨独立用户回合恰恰可能承载最大输入量，因此必须在本需求内成为硬观测边界，而不是后续扩展。

OpenAI、Anthropic、DeepSeek、Kimi、OpenRouter、LiteLLM、OpenTelemetry 和 Langfuse 的现有实践进一步表明，缓存读取、缓存写入、普通输入、请求结构连续性和 provider 实际结果必须分开表达；不同 provider 的原始 token 口径也不能未经归一化直接聚合。详细来源、当前代码锚点和证据局限见 [evidence-index.md](./evidence-index.md)。

因此，本需求先建立统一、可解释、隐私安全的 prompt-cache 可观测与聚合基础，再用真实 baseline 对剩余机会排序。具体缓存行为优化根据证据拆分为后续独立需求，不在缺少可靠分母和归因时提前承诺整体命中率目标。

## 目标

- 让每次进入 provider 的模型请求产生语义一致的缓存观测，能够关联运行、调用位置、实际模型与 provider 路由，同时不记录 prompt 或工具正文。
- 让同 session 新用户回合的第一次 provider 请求与上一用户回合最后一个可比较请求建立 lineage；同进程精确比较 final wire，跨进程至少提供分类级、明确降级的比较。
- 将缓存机会、请求结构连续性、provider 缓存读写结果和成本/时延结果分层表达，避免用单一命中率掩盖不同机制。
- 统一不同 provider 的输入 token 口径，保留缓存读取、缓存写入、其他输入、总输入、输出及其来源、缺失和推导状态，防止聚合双计数。
- 提供工程侧可重复执行的采集与聚合入口，能够按关键维度生成总量、比例、覆盖率、原因分布和未知项。
- 建立绑定代码、构建、模型、配置、时间窗口与样本范围的真实 baseline，识别贡献最大的未命中输入机会桶。
- 形成有证据支持的优化优先级和后续需求边界，而不是在本需求内混入多个尚未确认根因的行为改动。

## 范围

### 包含

- Agent 正常运行中所有经统一模型调用边界发出的 provider 请求，包括桌面对话、IM、voice、OpenAI compatible / AG-UI 和系统主动运行等既有入口。
- 当前 DeepSeek、Kimi、OpenRouter 路由及未来 provider 可使用的公共缓存观测语义；provider 特有字段可以保留为扩展，但不能改变公共字段含义。
- provider 返回的总输入、缓存读取、缓存写入、未读取/其他输入、输出、usage 来源、实际模型、实际路由和可用成本信息。
- adapter-input 与 final-wire 两级内容无关指纹、连续性关系、首次分歧类别、重建原因、调用序号、请求规模、空闲间隔和必要 transport 元数据。
- 跨用户回合和应用重启续开时的 comparison scope、断点域、变化标记、公共前缀规模和安全的丢失机会估算。
- 将“请求是否发生结构变化”与“provider 是否实际读取缓存”组合解释的稳定状态与原因分类。
- 工程侧的结构化采集、持久化或诊断导出、聚合报告和确定性 fixture；具体存储位置和脚本形态由技术方案确定。
- token 加权缓存读取比例、请求级非零读取比例、usage 覆盖率、缓存写入量、未读取输入量、成本与时延分布，以及明确的分子、分母和未知项。
- 按 provider、请求模型、响应模型、路由、交互入口、调用类型、调用序号、请求规模、工具数量、连续性状态、重建原因和时间间隔等维度分段。
- 独立报告 `cross_user_turn` 请求数、rebase 比例、估算未复用输入 token 贡献和主要 `first_divergence_domain`，不能被同 run 的高命中工具续轮稀释。
- 新用户回合、无工具对话、单步/多步工具循环、compaction、恢复、session 续开和应用重启等代表性场景的 baseline 采集计划与证据归档。
- 根据 baseline 输出未命中输入机会排序、证据强度、下一项区分性实验和建议拆分的后续需求。

### 不包含

- 不在本需求中实现跨独立用户回合 transcript 复用、调整 prompt 结构、固定工具集合、修改 compaction/recovery 行为、改变 provider 路由或新增显式 cache breakpoint；这些属于 baseline 后的候选优化。
- 不承诺 dashboard 整体、某个自然时间窗口或所有 provider 达到固定缓存命中率。
- 不新增面向普通用户的缓存命中率 UI、开关、设置页或通知。
- 不要求引入 OpenTelemetry、Langfuse 或其他外部观测平台；可以借鉴其语义契约，但是否接入外部系统不属于本期。
- 不使用缓存优化绕过上下文预算、工具协议、reasoning replay、权限刷新、恢复或回答正确性要求。
- 不记录完整 prompt、用户正文、工具参数/结果、附件内容、reasoning 内容、API key 或其他凭据。
- 不自动调用真实 LLM。任何需要新请求的 baseline 或 smoke 继续按单次授权冻结模型、场景和调用量。
- 不把 provider dashboard、计费账单或单次受控 smoke 作为产品内逐请求结构归因的替代品。

## 核心需求

### R-1 · 全链路覆盖与身份

- 所有生产模型调用入口必须经过同一缓存观测语义，不能只有桌面主对话或某个 provider 能被聚合。
- 每条观测至少能够关联代码/构建身份、run、session 的隐私安全引用、模型调用序号、交互入口、调用类型、请求模型、响应模型和 provider 路由。
- 请求重试、fallback、恢复调用和工具续轮必须能区分逻辑调用与 transport attempt，不能把重试误算成独立用户回合。
- 无法取得某项身份时必须显式标记缺失，不能使用默认模型或预期路由冒充实际结果。

### R-2 · Provider usage 归一化

- 公共观测必须分别表达总输入、缓存读取、缓存写入、未读取/其他输入和输出；缺失值、明确 `0` 和推导值互不混淆。
- 对 provider 返回包含式总量的情况，聚合前必须转换成不重叠的组成部分；对 provider 返回互斥组成部分的情况，不得再次相减。
- 当 provider 提供的信息足以完整对账时，各输入组成部分之和应与总输入一致；信息不足时保留部分观测和 coverage gap，不猜测未知缓存写入。
- DeepSeek 的 cache miss、Anthropic 的 breakpoint 后输入、OpenAI/OpenRouter 的 cache write 等原始语义应保留来源，不能因为统一命名而被错误等同。
- provider 原生值优先于本地估算；任何推导字段都要记录推导规则和来源，且不得用于冒充 provider 计费事实。

### R-3 · 请求结构诊断

- 缓存观测必须区分 adapter-input 结构与最终 provider wire 结构；判断 provider 可复用前缀时以 final wire 为权威。
- 对可比较的连续请求，应识别结构稳定、严格延续、相同请求重试或发生重建，并给出最早可解释的分歧类别。
- 分歧类别至少能够覆盖模型/路由变化、system 或 instructions 变化、工具集合或 schema 变化、历史消息变化、其他请求参数变化、上下文治理重建和诊断不可用。
- 可以提供内容无关的丢失规模估算，但必须标注其为机会量级而非真实 token 账单。
- 一次请求存在多个变化时优先报告最早分歧，同时保留“后续原因可能被遮蔽”的边界，不能宣称已经穷尽全部原因。

### R-3A · 跨用户回合 lineage

- 同 session 每个新用户回合的第一次 provider 请求，必须优先与上一用户回合最后一个成功且可比较的 provider 请求建立 lineage；同一 run 的工具续轮继续独立标记为 `within_run`。
- 每条 observation 必须显式提供 `comparison_scope`，至少区分 `within_run`、`cross_user_turn`、`cross_process_resume` 和 `unavailable`；不可比较不得继续写成普通 `baseline` 或未断裂。
- 同进程跨用户回合保留内存级、内容无关的 final-wire item digest，仅用于当前 session 比较；不得写入 Session、日志或 bundle。比较至少输出 `cross_turn_relation`、`common_prefix_items`、`first_divergence_domain`、`first_divergence_index/role`、`lost_prefix_items` 和安全的未复用输入 token 量级估算。
- 变化标记至少覆盖 system、persona、tools/Skill、messages/history、runtime context、request settings 和 model/provider route；标记应区分正常尾部追加新用户消息与移动、替换或删除旧前缀。
- 应用重启或 session 续开后，不持久化逐 item digest。可以持久化带版本域分离的分类级 fingerprint、计数、角色分布、最近 input token 和实际模型；此时 scope 为 `cross_process_resume`、precision 明确降级，公共前缀和精确断点不可得时保持 unavailable。
- 上一用户回合没有成功 provider 请求、旧 schema 不兼容或安全 fingerprint 不足时 scope 为 `unavailable`；不得用首次请求、跨进程恢复或缺失证据伪造严格延续。

### R-4 · 机会、结构与结果分层

- 首次请求、跨生命周期不可比较请求、可比较请求和已知不具备 provider 缓存条件的请求应明确区分。
- 请求结构稳定并不保证 provider 实际命中；provider 明确零读取、非零读取、首次写入、usage 缺失和服务端缓存可能过期应分别表达。
- 结构变化与 provider usage 的组合必须能回答“客户端破坏了复用机会”“请求保持稳定但 provider 未命中”“当前无法判断”或“按预期命中”等不同结论。
- provider best-effort、缓存构建延迟、TTL、最小输入长度和 sticky routing 等外部条件只在有实际证据时参与解释，不能从单次零读取反推确定根因。

### R-5 · 采集、持久化与导出

- 同一逻辑观测在实时日志、session 持久化诊断、diagnostic bundle 和真实 smoke 中使用一致的字段语义与 schema revision；允许不同出口只保留各自需要的子集。
- 至少提供一个受治理的工程入口，把原始观测聚合成机器可读结果和人可读摘要，不要求开发者手工拼接多种日志格式。
- 采集失败或部分字段不可用不能阻断正常对话，但必须在 coverage 中可见。
- 历史记录应能识别 schema revision 和代码/构建身份；不支持的旧记录应安全降级，不能静默套用新口径。
- 观测保留范围与生命周期遵守现有日志和诊断契约，不另建不可治理的永久用户行为仓库。

### R-6 · 聚合口径

- 整体缓存读取比例使用汇总后的缓存读取 token 除以汇总后的总输入 token，并同时报告分子、分母和 coverage；不得对单请求比例做简单平均冒充整体比例。
- 请求级指标与 token 级指标分开报告，避免大量小请求掩盖少量超大请求，或反之。
- 缓存读取、缓存写入、未读取输入、输出、成本、首事件/首文本时延和总时延均保留绝对量与分布，不以单一百分比代替。
- 成本优先使用 provider/gateway 返回的实际值；使用价格表推导时必须绑定价格版本并标明为估算。
- 时延与命中同步变化只能先报告相关性；需要因果结论时使用固定模型、请求与环境的受控比较。
- 所有聚合均显式保留 unknown/unavailable 样本，不能为得到完整比例而把缺失缓存计数当作零。

### R-7 · Baseline 与机会排序

- baseline 必须绑定代码 commit、未提交 overlay、build/runtime identity、provider、请求/响应模型、关键配置、采集时间窗口、样本数量、场景覆盖和排除条件。
- baseline 同时包含自然产品路径和确定性/受控场景：前者用于发现真实机会分布，后者用于验证归因和聚合机制；两类证据不得混为同一总体样本。
- 首轮 baseline 至少能区分首次用户回合、同 run 工具续轮、无工具多轮、上下文重建/压缩、恢复以及跨进程/session 续开等主要生命周期。
- 机会排序以未读取输入 token 贡献、可修复性、证据强度、成本/时延影响和正确性风险共同判断，不只按失败请求数量排序。
- 每个高优先级机会桶应给出当前证据、仍可成立的替代解释和下一项最有区分力的检查。

### R-8 · 隐私与安全

- 指纹只使用带版本域分离的不可逆 digest；日志和聚合不保存 message digest 列表、正文片段或可用于恢复内容的中间值。
- session、用户、provider request 等标识仅保留关联所需的最小安全引用；外发报告不得包含原始本机或生产标识。
- 不记录 prompt、system、工具定义正文、工具参数/结果、附件内容、reasoning、Authorization、API key 或签名头。
- baseline artifact 如包含原始日志，必须与可提交的脱敏摘要分离，并记录存放位置、访问边界和完整性；公开仓库只保存安全汇总。
- 观测与报告不得改变 provider 请求内容、权限、数据保留策略或正常回答行为。

### R-9 · 后续需求分诊

- 本需求完成时必须给出当前有效的综合判断与优化机会排序，而不是只交付一个没有结论的日志入口。
- 每个候选优化明确其 repair owner、受影响 provider/场景、质量 guardrail、预期收益口径和重新验证要求。
- 语义完整且根因不同的优化拆成平级独立需求；不因同属“缓存”而把跨回合复用、工具 schema 稳定、provider 路由和 compaction 策略混入一个实现。
- 如果 baseline 证明现有 102 机制已经覆盖主要机会，应如实记录并停止扩张，不为了需求存在而制造优化代码。

## 验收标准

- [ ] **AC-1 · Usage 归一化矩阵**：确定性 fixture 覆盖包含式总输入、互斥组成部分、cache read、cache write、DeepSeek hit/miss、明确零、字段缺失、非法值与推导值；完整样本可对账，信息不足样本保留 coverage gap，聚合不双计数。
- [ ] **AC-2 · 读写完整性**：缓存读取与缓存写入从 provider response 经过统一协议、stream/non-stream 消费、持久化诊断和聚合出口均不丢失；provider 不支持写入计数时保持 unavailable。
- [ ] **AC-3 · 结构/结果组合诊断**：自动化覆盖首次请求、严格延续并命中、严格延续但零读取、system/tools/messages/其他参数变化、rebase、usage 缺失和诊断不可用；每种组合得到稳定且不越界的解释。
- [ ] **AC-4 · 全入口关联**：桌面、IM、voice、OpenAI compatible / AG-UI 和主动运行的代表性自动化路径证明能够产生同语义观测并关联 run/call；不适用或无法构造的入口有明确证据说明。
- [ ] **AC-5 · 聚合数学**：固定数据集验证 token 加权比例、请求级比例、cache read/write/other 绝对量、coverage、分段和 unknown 处理；报告总量可从明细确定性复算。
- [ ] **AC-6 · 工程报告**：受治理入口能输出机器可读结果与人可读摘要，至少支持按 provider/model、入口、调用类型、调用序号、请求规模、结构状态和重建原因分段，并显示分子、分母、样本数和缺失数。
- [ ] **AC-7 · 隐私与兼容**：敏感内容扫描与测试证明日志、session 诊断、bundle 和报告不含 prompt、工具正文、reasoning、凭据或原始生产标识；旧 session/provider 缺少新字段时对话正常完成。
- [ ] **AC-8 · Baseline 证据**：完成一轮绑定 revision、runtime、provider/model、时间窗口和样本范围的 baseline；自然产品样本与受控样本分开报告，未执行的真实 provider 场景明确标注。
- [ ] **AC-9 · 机会排序与分诊**：证据索引给出未命中输入的主要机会桶、贡献量、置信度、替代解释和下一项检查，并明确哪些方向进入后续需求、暂缓或不做。
- [ ] **AC-10 · 正确性门禁**：观测和聚合不改变 provider 请求、上下文治理、工具协议、恢复语义或用户回答；受影响的 provider/context/session 测试和项目完整检查通过。真实 LLM 仅在单次明确授权后执行，且不替代确定性测试。
- [ ] **AC-11 · 连续无工具用户回合**：同 session 连续两个独立无工具用户回合的第一 provider 请求以 `cross_user_turn` 比较上一回合最后请求；旧 wire 前缀严格保留时报告 strict extension，否则准确报告首次断点、公共前缀与丢失规模，不退回普通 baseline。
- [ ] **AC-12 · 跨回合断点域矩阵**：分别改变 persona/system、runtime context、Skill/tools、request settings 和 model/provider route，并覆盖 history/message 移动、替换或删除；每项得到稳定变化标记和不越界的 `first_divergence_domain`。
- [ ] **AC-13 · Session 续开与应用重启**：同进程 session 续开保持精确 item 比较；新进程只恢复分类级 fingerprint，scope=`cross_process_resume`、precision 降级，精确 common prefix/lost items 不可得时保持 unavailable，旧 schema 正常降级。
- [ ] **AC-14 · 跨回合隐私与聚合**：Session、日志、bundle 和报告不保存逐 item digest、prompt、工具正文或可逆内容；报告能单独复算 cross-user-turn rebase 的请求数、估算 token 贡献和主要断点域。

## 方案阶段约束

- 设计前必须对照当前 provider 最终 response usage、实际 wire witness 和所有模型调用入口，不能只根据公共 `LLMUsage` 类型推断完整链路。
- 公共字段优先参考 OpenTelemetry 的 GenAI usage 语义和成熟观测平台的互斥 bucket 原则，但不为追求标准形式引入当前不需要的外部 telemetry 基础设施。
- Anthropic cache diagnostics 的“结构比较与实际读取分离”、最早分歧和内容无关指纹可作为主要参考；采用前仍需验证其约束是否适合 agent-friend 的 run/session 生命周期。
- provider 文档与 SDK 字段是动态外部事实，技术方案必须记录访问日期、项目实际 LiteLLM/OpenAI SDK 版本和本仓库代码锚点。
- baseline harness、production observation 和聚合报告的代码身份分开记录；改变采样或指标计算后，不得继续声称与旧 baseline 使用同一口径。

## 相关需求

- [009 · 引擎层上下文管理](../009-engine-context-management/requirement.md)
- [043 · Kimi Provider Integration](../043-kimi-provider-integration/requirement.md)
- [063 · 上下文压缩可靠性](../063-context-compaction-reliability/requirement.md)
- [094 · DeepSeek Vision 与 Files API](../094-deepseek-vision-and-files-api/requirement.md)
- [095 · Agent run 韧性恢复](../095-resilient-agent-run-recovery/requirement.md)
- [101 · 会话推理强度控制](../101-session-reasoning-effort-control/requirement.md)
- [102 · Provider transcript 前缀复用](../102-provider-transcript-cache-reuse/requirement.md)
- [108 · Agent 控制的 Skill 激活生命周期](../108-agent-controlled-skill-activation-lifecycle/requirement.md)

## 开放问题

- 统一缓存观测的权威 owner 应落在 `llm_providers`、Agent run telemetry 还是二者之间的协议边界，留待技术方案结合现有责任域决定。
- 原始逐请求观测应只进入现有日志/Session metadata，还是增加独立的本地受治理诊断存储，需要结合跨进程 baseline、保留周期和 diagnostic bundle 体积评估。
- provider/gateway 返回的实际 cost、项目价格表推导值和无成本字段的 provider 应如何建立统一优先级，留待方案阶段定义。
- final-wire witness 能否在不保存 item digest 列表的前提下提供有用的分歧规模估算，需要用当前 transport hook 做隐私与精度验证。
- 首轮自然 baseline 的时间窗口和最小样本量应根据本地可获得数据决定；需求不预设统计显著性，但任何稳定性结论都必须与实际样本强度相称。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-29 | 初版确认：首期交付统一观测、工程聚合、真实 baseline 与机会排序；纳入 cache read/write 和互斥输入口径；不做用户 UI，不在本需求实施具体缓存行为优化 | Phase 1 |
| 2026-08-29 | 跨独立用户回合缓存断点升格为硬要求：同进程精确 final-wire lineage、跨进程分类级降级、断点域/损失估算与独立聚合进入 109；不留作后续优化需求 | Phase 1/2 回溯 |
