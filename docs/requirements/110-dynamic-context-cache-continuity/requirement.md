# 110 · 动态上下文缓存连续性

> Dynamic Context Cache Continuity

## 状态

CONFIRMED

## 背景

[109 · Prompt cache 可观测性与优化基线](../109-prompt-cache-observability-and-optimization/requirement.md) 已经建立跨用户回合的 final-wire 连续性观测，并在真实长会话 smoke 中稳定观察到：普通用户回合的消息历史正常追加，但最早分歧域为 `runtime`。cold-ish 第二请求的缓存读取为 `32768/37093`（`88.34%`），warm 三轮第三请求为 `36736/37180`（`98.81%`）。这些结果说明现有请求仍能复用大部分稳定前缀，但每轮临时重建的动态上下文会在跨用户回合形成 rebase；`lost_prefix_tokens_estimate` 又只是按 item 比例计算的机会量级，不能被当作 runtime item 的真实 token 大小。

当前基础 system prompt 中的时间政策已经稳定，不包含动态时间；真正破坏连续性的是每次 provider 推理前临时生成的 model-visible runtime 消息。该消息会刷新精确到秒的时间，并改变 run、sequence 和 supersedes 等字段，却不会进入可供下一用户回合继续扩展的 model transcript。下一回合重新装配时，上一请求尾部的 runtime item 被新的 assistant/history 位置取代，于是即使业务历史没有重写，provider 仍看到旧前缀被重排。

同类问题不仅存在于时间。surface、权限、runtime capability、Skill、Memory 和未来动态工具都可能随运行状态或当前请求变化。如果这些 contributor 每轮重建 system 头部、替换旧 item，或者只在当前装配中临时存在，就会反复破坏依赖公共前缀的 provider cache。另一方面，为缓存而继续使用过期时间、权限或动作参数又会损害正确性和安全性。

本需求因此让 session 在进程内持有一份统一的 model-facing conversation history，把已经实际进入模型上下文的动态事实、assistant output 和 tool transaction 纳入后续请求的追加历史；稳定事实保持不动，变化事实以不可变 delta 表达，需要精确当前值的行为在动作发生时读取宿主可信来源。详细基线、代码锚点和同类实现证据见 [evidence-index.md](./evidence-index.md)。

## 目标

- 在同一 cache epoch 内，让连续普通用户回合尽量保持从第 `0` token 开始的真实公共前缀，而不是只依赖 cache key 或 provider 特有断点。
- 让上一请求实际进入 final wire 的 model-visible 动态上下文参与下一请求组装；已进入 transcript 的旧 item 不删除、不移动、不替换。
- 将动态事实统一表达为首次快照与后续 append-only delta，同时保持权限、时间和工具执行的宿主侧正确性。
- 让模型平时只获得足够进行日历理解的粗粒度日期与 timezone；需要“此刻”的推理和动作必须读取宿主可信 action-time。
- 明确 persona、system、核心工具契约、request settings、model route、compaction 和无法精确恢复的进程重启是 cache epoch 边界，不冒充普通动态变化。
- 延续需求 109 的内容无关观测，用确定性测试和真实长会话 smoke 共同验证前缀连续性、缓存读取与总输入变化。

## 原则

- 稳定前缀不动。
- 动态事实按需读取；需要公开的变化只追加 delta。
- 正确性优先于缓存：权限由宿主强制执行，时间敏感动作读取 action-time，过期快照不能冒充当前值。
- 旧 snapshot 可以保留；后续 delta 明确其适用范围和最新状态，不回写旧 item。
- provider capability 可以增强缓存表现，但核心 transcript 正确性不依赖任何单一 provider。
- 不为缓存优化新增 prompt、Memory 正文、工具正文、可逆 digest 或敏感 runtime 内容的持久化。
- compaction 是有意的 history rewrite 和 cache epoch，用于控制总输入成本；高命中率不是唯一优化目标。

## 范围

### 包含

- session 级、进程内的 model-facing conversation history，以及它与持久化业务 Session、当前 run continuation 和 provider final wire 的一致性边界。
- 普通用户回合、同一用户轮的工具续轮和系统允许的主动运行之间的 append-only 组装关系。
- 当前日期和 timezone 的低频粗粒度快照，以及跨日、timezone 变化和新 cache epoch 时的更新语义。
- 宿主可信时钟边界，以及取得当前日期时间的明确工具能力。
- 时间计算、相对日期理解、提醒/计划创建和其他时间敏感动作的 action-time 正确性。
- surface、权限、runtime capability、Skill catalog、Skill 激活指令、临时 filesystem scope 和 Memory recall 的统一 contributor/delta 语义。
- capability catalog 不再因可调用工具状态而改写稳定 system prompt 头部的行为要求。
- persona、system、核心工具 schema、request settings、model/provider route、compaction、session 续开和应用重启的 cache epoch 语义。
- DeepSeek 自动公共前缀缓存所需的严格连续性，以及 OpenAI/Anthropic 等 provider 可选显式 breakpoint 的 capability 层适配边界。
- 需求 109 observation/report 的延续使用，以及优化前后真实长会话 smoke 的同口径报告。

### 不包含

- 本需求不实现通用 deferred tool、tool search、Skill/MCP 动态工具 schema 发现或任意运行时工具注册；这些能力后续单独交付，但必须能够接入本需求定义的 contributor/delta 边界。
- 不为跨进程精确延续而持久化 model transcript、prompt、Memory recall、工具参数/结果、Skill 正文或敏感 runtime 内容。
- 不以加密、hash、digest 或 cache key 的名义持久化可被逆向、枚举或关联恢复的敏感正文替代物。
- 不承诺所有请求获得固定缓存命中率，也不把 provider 的 cold/warm、TTL、路由和 best-effort 行为归因成本地 transcript 的确定结果。
- 不为了减少上下文增长而在本期引入 Memory 引用、去重、摘要或选中项复用机制。
- 不自动执行真实 LLM smoke；每次真实调用仍按 `llm-api-confirm` 在执行前说明模型、场景和预计调用量并取得明确授权。

## 核心需求

### R-1 · Session model history

- 每个活跃 session 在进程内维护一份与 provider 可见顺序一致的 model conversation history，记录本 cache epoch 中已经实际进入模型上下文的不可变 item。
- provider 成功返回后，实际 assistant 输出以及后续工具调用、工具结果和 model-visible context delta 必须按真实顺序进入同一份 model history，供下一次请求继续扩展。
- 在动态 contributor 没有变化时，新用户回合的 final wire 必须以旧 final wire 为严格前缀，只在尾部追加上一 assistant 输出和当前用户消息；不得重新插入一份 runtime snapshot。
- 有动态变化时，变化 delta 应在语义对应的用户输入或工具输出边界追加；旧 snapshot 保持原位置。
- query-dependent context 可以在当前用户消息之后追加，但进入 final wire 后即成为该回合的不可变 transcript item，后续请求不能删除、移动或改写。
- model history 只表达 provider-facing transcript 的连续性，不取代业务 Session 的事件存储，也不得导致 model-only 内容被错误投影为用户可见聊天消息。

### R-2 · 粗粒度日历上下文

- 每个新 cache epoch 首次需要模型上下文时，提供一次宿主本地日期和 timezone；不注入小时、分钟、秒、run、sequence 或仅服务装配过程的可变标识。
- 同一日期和 timezone 内，普通用户回合与工具续轮不得因时间流逝重复追加日期或当前时间消息；跨分钟本身不触发 context delta。
- 宿主本地日期跨日时，在下一个用户输入或工具输出边界追加一条日期 delta；timezone 改变时追加包含新 timezone 及其当前本地日期的 delta。
- 长工具循环如果跨日或发生 timezone 变化，只在符合边界时追加一次相应 delta；不在工具执行中途打断当前动作，也不周期性注入精确时间 reminder。
- compaction、新 cache epoch 或无法精确恢复的 session 续开重新建立一次粗粒度日期/timezone 基线。

### R-3 · 当前时间与 action-time

- 系统稳定政策必须明确告诉模型：上下文中的日期/timezone 只用于粗粒度日历理解，不代表当前时钟；凡是需要具体当前时间或依赖当前时刻的判断，应使用专门的当前时间能力。
- `datetime_calculate` 提供明确的 current/now 查询能力，返回工具执行当下从宿主可信时钟读取的当前日期、时间和 timezone，而不是推理开始前保存的快照。
- “现在几点”“此刻日期时间”“从现在起多久”等请求必须通过当前时间能力取得可信值；模型不能从旧 snapshot、消息时间或推理开始时间猜测。
- “明天早上”等相对日历计算在需要权威解析时使用宿主可信时间；创建提醒、计划任务、相对延迟和其他重要时间敏感动作必须在实际工具执行时再次读取 action-time。
- 读取当前时间的工具结果作为普通工具结果进入 append-only transcript；后续请求可以看到旧结果，但旧结果的语义明确限定为其执行时刻，不再被当作当前值。
- model-visible 粗粒度日期与工具 action-time 不要求秒级一致；这种差异是显式语义分层，不能默默复用过期时间来追求表面一致。

### R-4 · 其它动态 contributor

- surface、权限和 runtime capability 在 cache epoch 首次出现时提供快照；值发生变化时只追加对应 contributor 的 delta，不重建稳定 system 头部或移动其它 contributor。
- model-visible 权限信息只用于帮助模型规划；真实允许/拒绝始终由宿主在动作执行时强制判断，旧权限 snapshot 不构成授权。
- Skill catalog 保留现有 fingerprint gating：catalog 不变时不重复追加，变化时追加新 catalog 状态。
- Skill 激活指令和临时 filesystem scope 仅在当前请求确实需要时加载；一旦进入 final wire 就保留在原位置。失效、替换或 scope 结束通过后续 delta 表达，不能删除旧指令。
- 每个非空 Memory recall 在当前用户消息之后原样追加为该回合的不可变 model transcript item；本轮没有召回时不追加 Memory item。旧 recall 留在其原用户回合位置，不做引用、去重、替换或移动。
- Memory 的出现、消失、内容变化和位置关系必须继续能由需求 109 的内容无关诊断区分；诊断能力不得要求持久化 recall 正文或可逆摘要。
- contributor 必须具有明确的类型、适用边界和失效语义，使未来动态工具能够复用相同的首次快照/append-only delta 模型，而不要求本期实现动态工具发现。

### R-5 · 稳定 system 与工具边界

- 稳定的 persona、system policy 和核心工具能力说明保持在 cache epoch 的固定前缀中；surface、权限、Skill 或其它动态能力不得通过重写 system prompt 头部表达。
- capability catalog 如果反映当前 callable tools，必须作为可独立变化的 model context contributor 处理，不能继续把整份可变 catalog 拼接进稳定 system prompt。
- 本期核心工具 schema 保持稳定。核心工具增删、schema 变化或其它会改变 provider `tools` 前缀的行为建立新 cache epoch，而不是伪装成普通 context delta。
- 后续 deferred tool/tool search 可以在 provider capability 层只追加按需发现的工具，但不得使核心正确性依赖某个 provider 的显式 cache breakpoint。

### R-6 · Cache epoch、恢复与 compaction

- persona、system prompt、核心工具 schema、request settings、model route 或影响前缀解释方式的 provider route 发生变化时，明确结束旧 cache epoch并建立新 epoch。
- compaction 是明确的 history rewrite/cache epoch；compaction 后的请求不得报告为旧 final wire 的严格延续。
- 同一进程内 session 暂停后继续，只有 model history 和相关上下文状态仍完整且可证明时才能标记 exact continuation。
- 应用重启、进程丢失或无法恢复精确 model-facing transcript 时，必须开始新 cache epoch并标记 degraded/new epoch；不得根据业务消息相似、cache key 或分类 fingerprint 冒充严格延续。
- 新 epoch 允许重新发出当前有效的首次快照；旧 epoch 的 provider cache 命中与否由 provider 自己决定，本地诊断不得伪造连续性。

### R-7 · Provider 适配

- DeepSeek 路由必须仅依靠从第 `0` token 开始的真实公共前缀获得自动缓存机会；cache key 不得被当作 transcript 连续性的替代品。
- 支持显式 cache breakpoint 的 provider 可以在 capability 层对稳定前缀、append-only transcript 或 deferred tool 定义利用 breakpoint，但不得改变核心 model history 顺序和 epoch 语义。
- cache key 只用于路由、命名空间和 provider 允许的负载分布优化；即使 key 相同，内容发生前缀分歧也必须如实观测。

### R-8 · 隐私、增长与可观测性

- 进程内 model history 可以保存完成当前 session 推理所必需的 model-visible item，但不得因此新增跨进程的 prompt、Memory、工具正文或敏感 runtime 持久化。
- 日志、Session 事件、诊断包和 prompt-cache observation/report 继续只保存内容无关分类、计数、不可逆安全标识和使用量；不得新增逐 item 正文或可逆 digest。
- append-only transcript 的上下文增长必须纳入 token 预算；需要改写历史时通过显式 compaction/cache epoch 完成，不允许在同一 epoch 内静默删除旧动态 item。
- 优化评估同时报告 cache read、first divergence 和总输入 token。不能以命中比例提升掩盖 append-only delta 导致的总输入持续增长。

## 里程碑

### M1 · Session model history 与可信时间

- 完成 session model history、普通用户回合和工具续轮的严格扩展关系。
- 完成粗粒度日期/timezone、跨日与 timezone delta、当前时间工具和 action-time 工具执行语义。
- 建立统一 contributor/delta、epoch、恢复和隐私边界，并使现有 surface、权限、Skill、Memory 能按该边界接入或被确定性验证。
- 保持核心工具集合稳定；不实现通用动态工具发现。

### 后续独立需求 · Deferred tools

- 基于本需求预留边界评估 Skill/MCP 动态工具发现、tool search、provider deferred tool schema 和按需追加。
- 后续需求必须单独确认工具生命周期、权限、provider 兼容性、schema 缓存和上下文预算，不能由本需求自动扩张实施范围。

## 验收标准

### AC-1 · 普通用户回合严格扩展

给定同一 session、同一 cache epoch、连续两个普通用户回合，且 persona、system、核心工具、settings、route 和所有动态 contributor 均未变化，则第二回合首个 final wire 以第一回合最后一个 final wire 为严格前缀，只追加第一回合 assistant 输出和第二个用户消息；`first_divergence_domain` 不得为 runtime，不产生 runtime rebase。

### AC-2 · 工具续轮不重复时间上下文

同一用户轮包含一个或多个工具续轮时，在未跨日且 timezone 未改变的条件下，不因 provider inference 次数或工具边界重复追加日期、timezone 或当前时间消息。

### AC-3 · 分钟、日期和 timezone 边界

使用可控宿主时钟分别验证：

- 跨分钟但仍在同一日期/timezone：不追加时间 context delta；
- 跨日：在下一个允许边界只追加一次新日期 delta；
- timezone 改变：只追加对应 timezone 与当地日期 delta；
- 跨日与 timezone 同时改变：以一条自洽的新日历状态表达，不产生重复或顺序冲突。

所有场景中旧 snapshot 保持原位置。

### AC-4 · 当前时间与重要动作

用可控宿主时钟验证“现在几点”“明天早上”和“创建提醒/相对计划”场景：当前时间查询返回调用时刻；相对日期计算使用可信当前基准；提醒/计划在动作执行时重新读取宿主时钟。推理前快照即使故意设置为旧值，也不得影响 action-time 结果。

### AC-5 · 动态 contributor 局部追加

分别改变 surface、权限、runtime capability、Skill catalog、Skill 激活状态和临时 filesystem scope；每次只追加对应 contributor 的 snapshot/delta，不移动旧前缀、不重写 system 头部、不改变无关 contributor。真实权限检查仍以宿主当前状态为准。

### AC-6 · Memory 生命周期和诊断

构造 Memory recall 内容不变、内容变化、首次出现、随后无召回以及与当前用户消息位置关系的确定性场景。每个非空 recall 只作为对应用户回合的新不可变 item 追加，旧 recall 不删除或移动；需求 109 observation 能正确诊断 presence、content 和 position 变化，且输出不包含 Memory 正文或可逆摘要。

### AC-7 · Cache epoch

分别改变 persona、system、核心 tools/schema、request settings、model route 和影响前缀的 provider route，并触发 compaction；每种情况都建立明确的新 cache epoch，不报告为普通 contributor delta或旧 wire 的 strict extension。

### AC-8 · Session 续开与应用重启

验证同进程、model history 完整的 session 续开可以 exact continuation；应用重启或运行时 history 缺失后只能 degraded/new epoch。两者在 observation/report 中可明确区分，且重启场景不伪造 exact common prefix。

### AC-9 · 隐私与持久化

检查 Session、日志、diagnostics bundle 和其它新增/既有持久化路径：不得新增 prompt、Memory 正文、工具正文、Skill 指令正文、敏感 runtime item、逐 item transcript 或可逆 digest；应用重启后的精确降级不能通过新增敏感持久化规避。

### AC-10 · Prefix 与总输入回归

确定性 final-wire fixture 必须同时证明：无变化时严格扩展；单 contributor 变化时只在尾部追加对应 delta；跨 epoch 时明确不可比；compaction 后不伪装连续。

### AC-11 · 真实长会话对照 smoke

在取得本次明确授权后，使用与需求 109 可比较的真实长会话场景，对优化前后分别报告：

- 模型、provider route、代码/构建身份、场景、历史规模、请求轮数和 cold/warm 状态；
- `cache_read`、`input_total`、缓存读取比例、普通/未读取输入和总输入变化；
- `comparison_scope`、`cross_turn_relation`、`common_prefix_items`、`first_divergence_domain` 和内容无关 lost-prefix estimate；
- 普通用户回合、工具续轮、跨日/timezone delta 和 current-time 工具调用的结果差异；
- append-only delta 对总输入增长的影响与 compaction 边界。

smoke 不保存 prompt、response、Memory、工具正文或逐 item digest，也不得把 cold/warm 差异归因成单一代码效果。

## 质量与兼容性要求

- 新增或改动的 model history、时钟、delta、epoch 和隐私逻辑必须有确定性自动化测试，不使用真实系统时间制造不稳定断言。
- 所有现有入口共享相同 Conversation/provider transcript 语义；桌面、IM、voice 或主动运行不能因入口不同绕过 session model history 或 action-time。
- 现有 Session 可以继续读取；无法精确恢复旧 session model history 时按 new epoch 降级，不要求迁移敏感正文。
- provider 不支持显式 cache breakpoint 时功能和正确性不受影响。
- 完成实现后必须通过项目规定的完整检查；真实 LLM 只承担外部缓存行为的观察型验收，不替代确定性机制测试。

## 开放问题

- append-only Memory、Skill 和权限 delta 在自然长会话中的输入增长速度，需要在 M1 实现后用总输入与 compaction 频率共同评估；本需求不预设引用或去重方案。
- 不同 provider 的显式 breakpoint 最佳放置策略由技术方案给出兼容边界，但首期可以只保证 provider-independent 的严格公共前缀。
- 后续 deferred tools 是否需要独立 tool history、schema delta 或 provider 原生 search，需要在独立需求中依据 M1 数据重新确认。
