# 114 · LLM Provider Responses API 支持

## 状态

CONFIRMED

修订日期：2026-09-05。用户最终确认：审计全部现有模型、接入 Kimi K3、使用 DeepSeek 与 Kimi 做真实验证；OpenRouter 保留存量代码与配置兼容，仅隐藏用户入口并标注暂不维护。

## 背景

后续需求 115 将实现类似 Codex 的折叠式 Agent 执行过程：在一次 Agent run 中区分中间进展与最终回答，并允许支持该能力的模型使用新的多轮执行与总结交互，不支持时继续使用当前交互。

当前 Agent 只依赖项目自有的 `LanguageModel.stream(ModelRequest(...))` 接口，不直接读取 OpenAI、LiteLLM 或其它 Provider SDK 的响应对象。`llm_providers` 目前把流式调用统一按 Chat Completions 风格处理，无法取得 Responses API assistant output item 原生提供的 `phase`，也没有为下游提供“不感知 Provider/API 名称即可判断是否具备原生 assistant 分阶段能力”的稳定口径。

本需求只建立 `llm_providers` 对原生 Responses API 的正式支持、统一事件映射和下游能力声明。Agent 对 phase 的编排、持久化与展示，以及折叠式执行 UI，继续由后续独立需求负责。

## 目标

- 对具备且已经验证原生 Responses route 的模型，使 `LanguageModel.stream()` 实际调用原生 `/responses`，并消费原生流式 output item。
- 把 assistant output item 的原生 `phase` 原样映射到项目流事件，稳定区分 `commentary`、`final_answer` 与缺失值。
- 保持 `LanguageModel` 作为上层唯一模型调用边界，不让 Agent、Memory、Bridge 或 Frontend 判断 Provider/API。
- 为后续需求提供 provider-neutral 的 assistant 分阶段能力声明，使下游能够选择新交互或退化到当前交互。
- 保持不支持 Responses API 的模型及所有一次性直出调用的既有 Chat Completions 行为。

## 术语与能力口径

- **模型 route**：产品选择的 Provider 与具体模型形成的调用路径。Responses 能力属于 route，而不是只由模型名称或模型家族决定。
- **原生 Responses route**：能够证明请求实际到达 Provider 正式 `/responses`，并保留 Responses output item、function call、usage、终态及上游实际提供的 assistant phase 的 route。接口支持与原生 phase 支持是独立能力；接口没有提供 phase 时，事件保持 `None`。把 Chat Completions 转换成 Responses 外形的客户端兼容桥不属于原生 route。
- **assistant 分阶段能力**：由 `llm_providers` 暴露的 provider-neutral、类型化能力。至少区分 `native` 与 `unavailable`，且可由下游在发起流式调用前获得。
- **逐事件 phase**：`LLMTextDelta` 上的运行时事实，取值为 `commentary`、`final_answer` 或 `None`。它必须来自产生该文本的 assistant output item。
- **legacy 交互**：当前不区分 commentary/final answer 的文本流与展示行为。assistant 分阶段能力不可用时，后续需求应保留该行为。

静态能力只表示当前模型 route 能否提供可信的原生 phase，不保证每个 output item 都一定携带 phase。即使 route 声明 `native`，单个 item 缺失 phase 时仍必须输出 `None`，不得由静态能力、消息位置或其它事件推断。

## 范围

### 包含

- 在 `llm_providers` 内维护模型 route 的原生 Responses 能力与 provider-neutral assistant 分阶段能力。
- `LanguageModel.stream()` 根据所选模型 route 的能力选择原生 Responses 或既有 Chat Completions 路径。
- 把当前 `ModelRequest.messages`、`tools` 和通用 `settings` 转换为 Responses input、function tools 与对应请求参数。
- 解析 Responses 流式事件并关联：
  - response；
  - output item；
  - output text delta；
  - assistant item phase；
  - function call 及 arguments delta；
  - completed、failed、cancelled 与 incomplete 终态；
  - usage 与内容无关的 diagnostics。
- 把 Responses 文本、function call、usage、结束原因、错误、取消和不完整流归一化回既有 `LLMStreamEvent`、`LLMToolCallDelta`、`LLMTurnDone` 与项目错误契约。
- 为 `LLMTextDelta` 增加 `Literal["commentary", "final_answer"] | None` 语义的 phase；Chat Completions 文本始终为 `None`。
- 让绑定到模型 route 的 `LanguageModel` 对下游暴露 provider-neutral assistant 分阶段能力；下游不需要读取模型名称、Provider、endpoint、SDK 或 transport。
- 保持 `LanguageModel.generate()` 与兼容入口 `complete()` 走当前 Chat Completions 单文本直出路径。
- 使用 deterministic fake/mock 覆盖 API 选择、请求转换、事件映射、终态、错误、取消和既有行为回归。
- 使用请求 witness 或等价的确定性证据证明原生 Responses route 实际请求 `/responses`，而不是在兼容层内退回 Chat Completions。
- 对当前 catalog 逐模型审计 Responses 与 phase 能力，保留官方资料、锁定 SDK 版本和真实验证的证据边界。
- 新增 `kimi/kimi-k3` 可选模型，不改变既有默认模型；当前 K2.x 模型按其真实能力继续工作。
- 对主力 DeepSeek 与 Kimi 执行有界真实验证，覆盖原生流、函数调用、工具结果续接和实际 phase 字段；正式验收须通过项目公共模型接口。
- 暂停向用户暴露 OpenRouter：隐藏公开模型 catalog 和设置中的凭据入口，相关代码注明暂不维护；保留内部 catalog、transport、错误归一化、凭据和存量设置兼容。
- 不为 OpenRouter 增加 Responses 支持；旧配置不自动迁移、清空或删除，存量调用仍保持原路径。

### 不包含

- 不新增 Prompt 来要求模型输出 commentary 或 final answer。
- 不增加 marker、XML 标签、特殊前缀或其它 framing 协议。
- 不根据最后一轮、消息位置、tool call、finish reason、stop reason、静态能力或是否即将结束来推断 phase。
- 不修改 Agent 的行为 Prompt。
- 不设计或实现 Agent 对 commentary/final answer 的消费、编排、持久化、记忆、搜索或 IM 语义。
- 不为 Responses 修改 `agent_bridge` 或 Frontend 的展示协议，不把能力字段投影到 UI；允许 OpenRouter 入口隐藏及已确认的 DeferredLanguageModel 纯能力转发。
- 不实现计时、折叠、自动展开、最终总结或其它需求 115 的交互。
- 不要求不支持原生 Responses 的 Provider 获得 phase，也不使用 Chat Completions 模拟 phase。
- 不建设动态 Provider 插件系统或通用 transport registry。
- 不为 OpenRouter 接入或真实验证新能力，也不删除现有兼容代码。

## 功能要求

### 1. API 选择与能力所有权

- API 选择必须完全封装在 `llm_providers`。
- 原生 Responses 能力由项目显式、可审查的模型 route catalog/spec 信息维护；不得仅凭模型名前缀、Provider 名称或未经验证的 LiteLLM 动态元数据自动开启。
- 只有已经证明底层不会桥接回 Chat Completions 的 route 才能声明原生 Responses 能力。
- `LanguageModel.stream()` 对原生 Responses route 使用 `/responses`；其它 route 继续使用当前 `/chat/completions`。
- `LanguageModel.generate()` 与 `complete()` 对所有当前 route 保持 Chat Completions，不参与本需求的流式 API 选择。

### 2. 下游能力声明

- `LanguageModel` 必须提供稳定、类型化、provider-neutral 的 assistant 分阶段能力，至少区分 `native` 与 `unavailable`。
- 能力在模型 route 绑定完成后即可读取，并随会话模型切换产生的新 `LanguageModel` 实例更新。
- `native` 只表示 route 能传递上游原生 assistant item phase；`unavailable` 表示下游只能采用 legacy 交互。
- 下游不得为了判断新旧交互而读取 Provider、API、model 前缀、endpoint、transport 或 SDK 对象。
- 本需求只提供能力口径；如何把它投影到 Bridge/Frontend，以及如何选择、持久化和展示新交互，由需求 115 决定。

### 3. Output item 与 phase 关联

- Responses 流必须按稳定 item identity 追踪 output item，而不是假定只有一个 item 或只读取数组首项。
- assistant output item 的 phase 必须在该 item 的所有文本 delta 上保持一致。
- 同一 response 包含多个 assistant output item 时，每个 item 的 phase 独立关联，不得串到相邻 item。
- phase 缺失或不属于支持值时统一保留为 `None`，不得猜测或改写。
- Chat Completions 路径产生的所有 `LLMTextDelta` phase 均为 `None`。

### 4. Function calling 与现有 Agent 契约

- Responses function call 必须映射为现有 `LLMToolCallDelta`，继续提供稳定的 index、call ID、tool name 与 arguments JSON delta。
- 一个 response 中存在多个 function call 时，arguments delta 必须按各自 item/call identity 累积，不得交叉。
- 流结束时继续通过 `LLMTurnDone.assistant_turn` 提供现有 Agent 可消费的完整 assistant/tool-call 结果，不迫使 Agent 增加 Responses 专用分支。
- Responses function call output 的下一轮输入转换必须保持现有 tool call/result 关联，不改变 Agent 工具编排所有权。

### 5. Usage、终态、错误与取消

- Responses usage 必须映射到现有 `LLMUsage` 字段，并保持“未提供”与“明确为零”的区别。
- response completed、failed、cancelled 与 incomplete 必须有明确、可测试的统一语义；只有完整成功的响应才能产生正常 `LLMTurnDone`。
- Responses 错误必须进入现有 provider-aware 错误归一化，不向上层泄漏 SDK 类型或原始 Provider 对象。
- 用户取消或下游关闭 generator 时必须关闭原生流和 HTTP client，不能继续在后台消耗额度。
- 未产生业务输出前的同 API 瞬时故障可以沿用现有有界重试；已经产生文本或完整终态后不得自动重放。
- Responses 请求失败后不得静默切换到 Chat Completions。跨 API 回退不属于本需求。

### 6. 请求转换与兼容性

- `ModelRequest.messages`、`tools` 和 `settings` 继续是上层唯一请求输入；Agent 不构造 Responses SDK 对象。
- 转换必须覆盖当前实际使用的 system/user/assistant/tool 消息、assistant tool calls、tool results、文本及现有通用设置。
- 不被 Responses 支持或无法无损转换的设置必须明确拒绝或按已记录规则处理，不得静默改变语义。
- 保留的 Chat Completions 模型及 generate/complete 的请求参数、Kimi 专用流、重试、usage、工具调用和取消行为保持现状；OpenRouter 存量兼容路径同样保留。

### 7. 模块边界

- 生产实现原则上收敛在 `llm_providers` 的协议、事件、client、transport、catalog/spec/factory 与相应 tests。
- `agent/src/agent/conversation.py` 等上层文件只用于只读兼容性核对。
- 若无法通过 `llm_providers` 公共契约提供能力或事件，必须先提交真实阻塞证据并取得用户确认，才能修改上层模块。
- `llm_providers` 之外不得新增 Provider/API 判断。
- OpenRouter 公开入口隐藏属于用户明确追加范围；保留枚举与存量设置，不在上层新增 API 选择逻辑。

## 验收条件

- [x] **AC-1 原生 Responses 路由**：声明原生 Responses 能力的模型 route 在 `LanguageModel.stream()` 中选择 Responses transport，请求 witness 明确证明目标路径是 `/responses`，且未经过 Chat Completions bridge。
- [x] **AC-2 Chat Completions 路由**：未声明原生 Responses 能力的模型继续选择现有 Chat Completions transport，请求、重试、流式文本、工具调用和 usage 行为不变。
- [x] **AC-3 下游能力口径**：绑定模型后的 `LanguageModel` 能在调用前返回 `native` 或 `unavailable` 的 provider-neutral assistant 分阶段能力；测试证明下游无需判断 Provider、API 或模型名前缀。
- [x] **AC-4 commentary phase**：commentary assistant output item 的所有文本 delta 均带 `phase="commentary"`。
- [x] **AC-5 final answer phase**：final answer assistant output item 的所有文本 delta 均带 `phase="final_answer"`。
- [x] **AC-6 phase 缺失**：assistant output item 没有 phase 或 phase 不受支持时，所有对应文本 delta 的 phase 都为 `None`，且没有推断行为。
- [x] **AC-7 多 output item 隔离**：同一 response 包含多个 output item 时，文本 delta、phase 和 item identity 正确关联，不发生 phase 串线。
- [x] **AC-8 Responses function call**：单个及并行 function call 的 ID、名称和 arguments delta 正确映射到现有 `LLMToolCallDelta`，最终 `LLMTurnDone.assistant_turn` 可被现有 Agent 契约消费。
- [x] **AC-9 请求转换**：当前实际使用的 messages、tools 和 settings 能转换为正确的 Responses input/tool 结构；无法无损支持的输入明确失败，不静默丢字段。
- [x] **AC-10 usage 与正常结束**：Responses completed 事件的 usage、响应标识、模型标识和结束原因正确归一化，并且只发出一次 `LLMTurnDone`。
- [x] **AC-11 失败与不完整流**：failed、cancelled、incomplete、错误事件和缺少完整终态的流分别进入明确的项目错误/中断语义，不被误报为成功完成。
- [x] **AC-12 取消与资源释放**：响应头等待期、流迭代期和下游关闭 generator 时均可取消并释放原生 response/client，不继续后台请求。
- [x] **AC-13 安全重试边界**：同一 Responses API 的瞬时错误仅在未产生业务输出时有限重试；已经产生文本后中断不会重放；任何错误都不会切换为 Chat Completions。
- [x] **AC-14 generate/complete 保持直出**：原生 Responses-capable 模型的 `generate()` 与 `complete()` 仍调用 Chat Completions，并保持单个 `ModelResponse.text` 的既有语义。
- [x] **AC-15 既有回归**：Chat Completions、Kimi direct、DeepSeek、OpenRouter 存量兼容、prompt cache、reasoning continuation、request witness、usage、错误和取消测试保持通过。
- [x] **AC-16 边界检查**：生产代码中的 Provider/API 选择只存在于 `llm_providers`；Agent、Memory、Bridge 与 Frontend 没有新增 Responses/Chat Completions 分支。
- [x] **AC-17 项目门禁**：受影响的 deterministic tests 与项目规定的 `scripts/check` 全部通过；根据 frozen runtime 规则完成并记录 packaging 影响分类及其所需验证。
- [x] **AC-18 无隐式真实调用**：deterministic 自动化验证不访问真实 LLM；独立真实验证在执行前说明模型、目的、调用次数与预计 token，并匹配当次授权，不自动扩大次数或重试。
- [x] **AC-19 全 catalog 审计**：逐项记录当前 DeepSeek 三个模型、Kimi 四个 K2.x 模型及新增 K3 的 Responses/phase 结论与证据；OpenRouter 两个模型记录为隐藏、暂不维护。
- [x] **AC-20 主力 Provider 真实验证**：至少 DeepSeek V4 Flash 与 Kimi K3 通过 `LanguageModel.stream()` 完成原生 Responses 文本、工具调用及工具回传后的回答，保留 endpoint、模型、事件种类、phase 观测和 usage 的脱敏证据。没有返回 phase 只证明本次样本缺失，不得外推为模型永不支持；接口或凭据阻塞明确记为未通过。
- [x] **AC-21 OpenRouter 隐藏**：公开产品模型列表及设置凭据入口不再展示 OpenRouter，相关代码注明暂不维护；内部 catalog、兼容调用、凭据校验和存量设置保留。历史数据、其它设置与系统凭据不被自动删除或迁移。

## 关键信息

- 需求 115 是本能力的首个下游，但 114 的能力名称与事件契约不得绑定具体 UI 实现。
- 当前锁定依赖版本与其真实源码行为是设计和验证依据；不能把最新文档中的能力直接外推到仓库锁定版本。
- 真实连通性不是 deterministic 验收的替代品。设计期的原始 SDK 探测也不能替代实现后的公共接口 smoke；主力 Provider 的真实 smoke 已纳入本次验收。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-09-05 | 首次确认需求；限定 Responses API 用于能力匹配的流式调用，保留 generate/complete 的 Chat Completions 直出语义，并增加供需求 115 使用的 provider-neutral assistant 分阶段能力口径。 | 新需求 |
| 2026-09-05 | 用户确认新增 Kimi K3、要求主力 DeepSeek/Kimi 真实验证并移除 OpenRouter；逐模型审计不以文档缺少字段推断原生 phase 不可用。 | catalog、真实验收、OpenRouter 关联入口和旧设置兼容 |
| 2026-09-05 | 用户收窄 OpenRouter 范围为隐藏用户入口、保留代码并标注暂不维护，明确授权开始实现。此前删除及迁移方案被覆盖。 | 公开 catalog、凭据入口；保留存量兼容 |
