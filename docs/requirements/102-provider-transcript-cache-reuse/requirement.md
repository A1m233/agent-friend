# 102 · Provider transcript 前缀复用

> Provider Transcript Cache Reuse

## 状态

CONFIRMED

## 背景

agent-friend 已能持续携带会话历史完成多轮对话和工具循环，但“语义上仍在增长”不等于“provider 实际收到的请求保持前缀不变”。在 2026-08-22 的 DeepSeek 官网统计中，当日输入 token 为 `2,622,962`，其中命中缓存 `430,080`、未命中 `2,192,882`，按输入 token 计算的命中率约为 `16.4%`。这明显低于此前 Kimi 运行中观察到的整体水平。

现有运行证据进一步确认，问题主要不在消息数量、工具 schema 体积或 DeepSeek 缓存能力本身：

- 对完全相同请求执行受控重复调用时，覆盖短/长历史与有/无工具的四组 DeepSeek case，第二次调用均能命中约 `99.69%`～`99.93%` 的输入 token。
- 对现有真实运行日志取样时，连续模型请求之间普遍没有保持 provider 可见前缀；绝大多数首次分歧发生在新插入的 assistant 消息处，工具 schema 则保持稳定。
- 在相同历史、工具和真实 provider tool-call/result 条件下，当前“重建上下文尾部”的续轮请求命中为 `0%`；保留并延续真实 provider transcript 的实验请求命中 `41,984 / 42,097`，约为 `99.73%`。

因此，本需求不把“提高缓存命中率”理解为盲目调参数，而是要修复 Agent run 内连续模型请求的 transcript 演进方式：在不牺牲上下文正确性、工具协议、reasoning continuation、预算保护和恢复语义的前提下，使下一次 provider 请求能够复用上一次请求已经建立的稳定前缀。

详细调查数据、适用范围和证据局限见 [evidence-index.md](./evidence-index.md)。

## 目标

- 同一 Agent run 内的工具续轮和其他连续模型调用尽可能保持 provider 可见请求前缀稳定。
- provider 返回的 assistant 响应项、工具调用、工具结果及协议必需的 opaque reasoning continuation 能够按原始语义继续参与后续请求。
- trusted time、权限、文件系统 scope、记忆投影等运行时上下文在续轮中仍保持最新，不能为了缓存复用而冻结过期状态。
- 上下文压缩、请求包络变化、恢复与重试等不再满足安全复用条件的场景能够明确失效、重建或回退，不使用陈旧 transcript。
- 用内容无关的结构化诊断区分“可延续”“已重建”和“缓存实际命中”，让后续线上命中率变化可以被解释。
- 首期以 DeepSeek 真实工具续轮验证收益，同时保持 Kimi、OpenRouter 及其他 provider 的既有行为兼容。

## 范围

### 包含

- 单次 Agent run 从首次模型请求到完成、失败或取消之间的连续 provider 请求。
- 普通工具调用、多步工具调用、并行工具调用及工具错误后的模型续轮。
- provider 返回的真实 assistant/tool-call 结构、工具结果和需求 101 已定义的 opaque reasoning continuation 回放。
- 在稳定请求包络下保持可复用前缀；在运行时上下文刷新时让新状态对后续模型可见。
- 与既有上下文预算、压缩、工具结果保留、附件/视觉输入、模型运行快照和 run recovery 的协同。
- 当消息投影、模型、工具定义、provider 参数、压缩 checkpoint 或其他请求结构发生不兼容变化时的安全失效与重建。
- 对 transcript 延续状态、重建原因、请求指纹和 provider usage/cache usage 的脱敏诊断。
- fake/mock 机制测试和经单次明确授权的真实 DeepSeek 对照 smoke。

### 不包含

- 不承诺 DeepSeek 官网 dashboard、某日整体流量或所有请求达到 `95%` 缓存命中率；验收阈值只适用于本需求定义的受控工具续轮 case。
- 不在首期复用跨独立用户回合、跨 session、跨进程或应用重启的 provider transcript。
- 不改变 DeepSeek、Kimi 或其他厂商的缓存 TTL、最小可缓存长度、路由、计费或服务端淘汰策略，也不对 provider 侧实际命中作无条件保证。
- 不新增用户可见缓存开关、命中率 UI 或 provider-specific cache-control 配置页。
- 不改变长期记忆的召回语义，不为了缓存把上一回合的临时 memory、trusted time 或权限快照永久保留。
- 不取代需求 063 的上下文预算与压缩、需求 075 的工具结果保留、需求 095 的 run recovery 或需求 101 的 reasoning replay；本需求只补齐这些能力与 provider transcript 前缀复用之间的协同边界。
- 不以缓存优化为理由省略工具结果、隐藏 provider 错误、绕过上下文预算或降低用户可见回答正确性。

## 核心需求

### R-1 · 连续请求的前缀稳定性

- 在模型、工具定义、provider 参数和已发送 transcript 均未发生不兼容变化时，同一 run 的下一次模型请求应保留上一次 provider 可见请求作为严格前缀，并在其后追加新的响应项、工具结果和必要运行时上下文。
- 不得仅因为内部重新组装消息，就移动、改写或重新序列化已经发送给 provider 的稳定前缀。
- 前缀稳定性以最终 provider wire request 为准，不能只比较进入 `LLMClient` 前的领域消息对象。

### R-2 · Provider transcript 保真

- 后续请求必须使用 provider 实际返回并且协议要求回放的 assistant/tool-call 信息，而不是用语义相近但序列化不同的合成消息替代。
- tool-call id、函数名、参数、并行调用关系、工具结果配对以及失败终态必须保持协议完整。
- provider 要求回放的 opaque reasoning continuation 继续服从需求 101 的最小生命周期与隐私边界，不进入用户可见正文、搜索、memory、普通日志或错误详情。
- provider 不支持或不需要某类 transcript item 时，应保持该 provider 的现有合法请求行为，不能硬套 DeepSeek 专用格式。

### R-3 · 动态运行时上下文正确性

- trusted time、权限、文件系统 scope、临时 memory、surface 能力等运行时上下文发生变化后，下一次模型请求必须能够看到当前有效状态。
- 已发送的历史运行时快照不得被原地改写；后续模型能够区分旧快照与当前有效状态，不能继续依赖已经撤销的权限或过期信息。
- 如果某类上下文无法在保留旧前缀的同时安全刷新，系统必须放弃该次前缀复用并走明确的重建路径。

### R-4 · 安全失效与重建

- 上下文压缩或历史投影重写、模型/provider 变化、工具 schema 变化、请求级配置变化、附件表示变化及其他会改变既有 wire prefix 的操作，必须触发可解释的失效、重建或 provider-specific 回退。
- 失效与重建不能造成 assistant/tool 事件重复、遗漏或乱序，也不能让同一个工具结果被模型消费两次。
- 重建后仍须经过当前模型的完整上下文预算保护；缓存复用状态不能成为第二套绕过 `ContextManager` 的历史事实源。
- 无法确定旧 transcript 是否与当前运行状态兼容时，默认选择正确性优先的重建，而不是猜测继续复用。

### R-5 · Run 生命周期隔离

- transcript 复用状态只属于当前活动 run；run 完成、失败或取消后不得被下一次独立用户输入静默继承。
- 编辑重发、重新生成、切换 session 和应用重启不得复用已失效的活动 run transcript。
- 同一请求的有界 provider 重试应使用一致的 run 快照；若恢复动作改变了有效请求内容，应建立新的、可识别的请求基线。

### R-6 · 既有上下文治理兼容

- 首次模型请求、工具续轮、恢复续轮和收尾请求继续经过需求 063 定义的统一预算保护。
- 工具结果的原始事件、模型视图截断和 recoverable/pinned 语义继续以需求 075 与 082 为准。
- 发生 compaction 时，持久化事件流和 checkpoint 仍是会话事实；临时 transcript 状态必须与新的有效模型视图重新对齐。
- 附件和 DeepSeek vision 的最终 wire 表示继续保持需求 094 的兼容边界。

### R-7 · Provider 兼容与降级

- 上层 run 语义保持 provider-agnostic；不同 provider 对 transcript item、reasoning replay 和 cache usage 的差异由既有 provider 边界吸收。
- provider 没有返回缓存 usage、没有提供 prompt cache，或缓存因服务端原因未命中时，对话仍应正常完成。
- Kimi、OpenRouter 和既有 DeepSeek 非工具请求不能因本需求退化；不能通过只对单个模型名称写死的全局分支改变其他 provider 请求。

### R-8 · 可观测与可验证

- 每次连续模型请求应能以不记录 prompt 正文的方式观察 run/request 标识、请求结构指纹、是否延续已有 transcript、是否发生重建及重建原因。
- provider 返回 usage 时，应分别保留输入 token、缓存命中输入 token、未命中输入 token和输出 token；缺失值与真实 `0` 不得混淆。
- 缓存比率的计算口径必须显式：本需求的真实 smoke 使用 `cached_input_tokens / prompt_tokens`，并同时报告分子、分母、模型、场景和调用序号。
- 诊断信息不得包含 API key、完整 prompt、工具敏感参数、用户正文或 opaque reasoning 内容。

## 验收标准

- [ ] **AC-1 严格前缀**：在固定模型、provider 参数、工具 schema 和稳定运行时包络的 fake provider 工具循环中，第 `N+1` 次最终 wire request 以前一次最终 wire request 为严格前缀，只在尾部追加 provider assistant 响应项、工具结果和后续有效上下文。
- [ ] **AC-2 transcript 保真**：单步、多步和并行工具调用测试证明 assistant tool-call、id、arguments、tool result、失败状态及 provider-required reasoning continuation 均按原始协议回放，没有合成替换、重复消费或配对断裂。
- [ ] **AC-3 动态上下文刷新**：在工具续轮之间变更 trusted time 或可测试的运行时 scope 时，后续模型能看到最新状态；旧前缀不被原地改写，已经撤销的能力不再作为当前权限生效。
- [ ] **AC-4 安全重建矩阵**：自动化覆盖至少包含 compaction/history rewrite、模型或 provider 变化、工具 schema 变化、请求配置变化和附件表示变化；每种情况均得到确定的继续复用或重建结果，并记录稳定原因码。
- [ ] **AC-5 生命周期与恢复**：完成、取消、失败、编辑重发、新用户回合、session 切换和应用恢复不会泄漏旧活动 run transcript；相同请求重试保持一致快照，内容发生变化的恢复建立新基线。
- [ ] **AC-6 上下文治理不绕过**：长历史、巨大工具结果和 compaction 后续轮仍通过既有预算与模型视图保护；原始 session 事件保持完整，最终请求不会发送已知超预算内容。
- [ ] **AC-7 DeepSeek 受控真实验收**：经本次明确授权，在与既有 proof-of-mechanism 等价的稳定 DeepSeek 工具续轮 case 中，第二次模型请求的 `cached_input_tokens / prompt_tokens >= 95%`；报告必须同时给出分子、分母、模型、工具数和消息规模。该阈值不外推为 dashboard 整体目标。
- [ ] **AC-8 无缓存降级**：fake provider 不返回 cache usage、明确返回零命中或不支持缓存时，run 仍正常完成，usage 归一化不会把缺失字段误报成命中。
- [ ] **AC-9 Provider 回归**：现有 DeepSeek、DeepSeek Vision、Kimi 和 OpenRouter 的请求构造、工具循环、reasoning replay 与 usage 测试继续通过；真实 Kimi 或其他 provider 调用不是自动化硬门槛，若执行须另行遵守 LLM API 授权。
- [ ] **AC-10 可观测性**：诊断能够关联同一 run 内的连续请求，区分延续、重建、provider 零命中和 usage 缺失，并且不记录 prompt、工具敏感参数或 reasoning 正文。
- [ ] **AC-11 项目门禁**：新增核心行为具有自动化回归覆盖，项目规定的完整检查通过；真实 smoke 只补充验证 provider 缓存行为，不替代确定性机制测试。

## 方案阶段约束

- 在确定 ledger、transcript snapshot 或其他实现形态前，先核对 DeepSeek 官方缓存与工具续轮契约，并调研其他成熟 Agent/provider 对“保留 provider response items、追加 tool results、刷新动态上下文、压缩后重建”的公开实践。
- 同类实践至少覆盖一个以 provider response item 为事实边界的实现，以及一个需要 preserved reasoning/thinking 回放的 provider；优先使用官方文档、官方源码和可复现协议证据，不以社区猜测作为设计依据。
- 调研用于验证约束是否相同、哪些做法可复用以及哪些不适合 agent-friend，不把“别人这样做”本身当作方案正确性的证明。

## 相关需求

- [005 · 工具调用机制与互联网搜索](../005-engine-tool-calling-and-web-search/requirement.md)
- [009 · 引擎层上下文管理](../009-engine-context-management/requirement.md)
- [043 · Kimi Provider Integration](../043-kimi-provider-integration/requirement.md)
- [063 · 上下文压缩可靠性](../063-context-compaction-reliability/requirement.md)
- [075 · 工具结果上下文保留](../075-tool-result-context-retention/requirement.md)
- [082 · 工具执行治理](../082-tool-execution-governance/requirement.md)
- [094 · DeepSeek Vision 与 Files API](../094-deepseek-vision-and-files-api/requirement.md)
- [095 · Agent run 韧性恢复](../095-resilient-agent-run-recovery/requirement.md)
- [101 · 会话推理强度控制](../101-session-reasoning-effort-control/requirement.md)

## 开放问题

- 活动 run 的 transcript 状态应由 Conversation、run orchestration 还是 provider adapter 拥有，留待技术方案结合现有边界决定。
- 动态运行时上下文应采用追加快照、可验证 delta 还是在特定变化时直接重建，需由真实 prefix、权限撤销和 token 成本证据共同决定。
- 不同 provider 的最小 replay scope、允许的 response item 形态和缓存 usage 字段并不一致，技术方案需要定义公共不变量与 provider-specific 能力边界。
- 首期明确不做跨独立用户回合复用；若后续数据表明首次请求仍占主要未命中 token，应以单独范围重新评估 session-wide transcript 与动态上下文语义。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-24 | 初版确认：首期限定单次 Agent run；受控 DeepSeek 工具续轮以 `>=95%` 为 case 验收线，不承诺 dashboard 整体比例；正确性优先，结构变化时安全重建；Phase 2 必须先调研官方契约与同类实践 | Phase 1 |
