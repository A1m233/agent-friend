# 114 · LLM Provider Responses API 技术方案

## 状态

CONFIRMED

用户已确认显式能力、流式 Responses、generate/complete 保持 Chat Completions、K3 接入，并授权开始实现及 DeferredLanguageModel 纯能力转发。OpenRouter 最终范围为隐藏公开入口、保留代码与存量兼容并注释暂不维护；下文早先删除方向以本次修订为准。

需求：[requirement.md](requirement.md)。资料与真实观测：[evidence-index.md](evidence-index.md)。

## 现状与调用链

`create_language_model()` → `build_provider_spec()` → `LLMClient`。上层传 `ModelRequest(messages, tools, settings, control)`；`LLMClient.stream()` 进入同 API 重试、单次流处理与统一 observation。现有单次流通过 LiteLLM Chat Completions，Kimi 则用直接 OpenAI SDK Chat 流保留 choice-level usage。

`generate()` 与兼容入口 `complete()` 返回单个 `ModelResponse.text`，调用方包括上下文摘要与记忆抽取。它们不参加本次 API 切换。Responses 支持非流式调用不等于本项目必须同时迁移两个方法。

现有 `ProviderAssistantTurn` 包含合并文本、完整工具调用和 `ProviderReasoningState`。Agent 的 `SessionModelHistory` 把它保存为项目 Message；Agent 不读 SDK 对象。`LLMToolCallDelta` 只在终态前累积，统一重试事件会让 Agent 重置临时工具累积。

`usage.py` 当前对只有 `input_tokens` 的对象按 Anthropic 独占分桶合计，不能直接把 Responses usage 塞入该分支：Responses 的 input_tokens 已包含 cache。必须增加明确的 Responses usage 入口。

## 文件与责任

| 范围 | 修改意图 |
| --- | --- |
| `llm_providers/src/llm_providers/protocol.py`、`stream_events.py` | 类型化能力、可选文本 phase；保持现有事件与请求入口 |
| `spec.py`、`catalog.py`、`factory.py`、`__init__.py` | route 能力装配、K3、公开 catalog 过滤暂不维护 Provider；保留内部导出 |
| `client.py` | 选择单次流实现，保持原 Chat 请求/重试路径与 generate |
| `responses/` | 新增内聚子目录：`request.py` 请求转换、`stream.py` item 状态机、`transport.py` SDK 生命周期、`__init__.py` 内部入口 |
| `usage.py`、`errors.py`、`error_normalization.py`、`reasoning.py` | 原生 usage、非成功终态及必要 reasoning 回放；不重写 Chat 逻辑 |
| `litellm_transport.py`、`credential_validation.py`、`kimi_transport.py` | 保留现有逻辑；只在真实复用需要时修改 |
| `llm_providers/tests/` | 请求、状态机、HTTP、重试、取消、能力与回归 |
| Frontend 设置凭据组件 | 隐藏 OpenRouter 凭据组并注释；保留 runtime_config/Bridge/Rust 枚举和凭据代码 |
| `agent_bridge/src/agent_bridge/runtime_config.py` | 已授权的最小越界：`DeferredLanguageModel.capabilities` 纯转发 |
| `scripts/responses-smoke/run.py`、`run.sh`、`run.ps1`、`scripts/README.md` | 单独显式运行的真实公共接口 smoke，默认无真实调用 |
| `.env.example`、`llm_providers/README.md` 与必要当前模块说明 | 更新有效入口与兼容边界；不重写历史需求记录 |

Responses 实现不修改 `agent/conversation.py`、Agent 行为 Prompt、Bridge 事件展示协议或 UI 执行过程。

## 能力与路由

### 私有 route 配置

模型 route 通过 `ProviderSpec.responses: ResponsesRoute | None` 显式声明原生 Responses：非空时携带 wire model、base URL、参数 profile 与 phase 能力；为空时保留 Chat，不另设重复的 stream_api 字段。Catalog 装配到 immutable ProviderSpec，不查询 LiteLLM 动态元数据来自动开通 Responses。目录外、自建 spec 缺少显式声明时保留 Chat 路径。

Responses 是否可用和 phase 是否有正面证据分别维护。原始 delta 即使来自尚未声明 native phase 的 route，只要对应 assistant item 实际有受支持值，也必须如实透传；静态能力不是过滤器。

| 产品模型 | stream API | 初始 phase 声明 / 证据边界 |
| --- | --- | --- |
| `deepseek/deepseek-v4-flash` | Responses | `native`；设计期实测 final_answer，尚未实测 commentary |
| `deepseek/deepseek-v4-pro` | Responses | 暂 `unavailable`；接口已由官方确认，phase 尚未逐型号实测 |
| `deepseek/deepseek-v4-flash-vision-exp` | Responses | 暂 `unavailable`；同上，图片输入转换须保留 |
| `kimi/kimi-k2.5` | Chat Completions | `unavailable` |
| `kimi/kimi-k2.6` | Chat Completions | `unavailable` |
| `kimi/kimi-k2.7-code` | Chat Completions | `unavailable` |
| `kimi/kimi-k2.7-code-highspeed` | Chat Completions | `unavailable` |
| `kimi/kimi-k3` | Responses | 暂 `unavailable`；已实测文本流，该样本 phase None |
| 两个原 OpenRouter 模型 | 原 Chat 路径 | 隐藏用户入口、暂不维护；存量配置兼容 |

这里的 `unavailable` 是当前产品没有足够证据承诺原生 phase，不能解读为厂商模型永远不支持。后续真实正面证据可更新具体 route 声明，不能由同族模型推断。

K3 使用独立的 reasoning 定义，支持 low/high/max，不复用 K2 的 thinking 开关。K3 上下文按官方 1M 声明；不改变既有 Provider 默认模型。其参数与附件能力仅开放当前转换及测试覆盖的部分，不直接复制整组 K2 能力。

### 下游口径

`protocol.py` 定义 frozen `LanguageModelCapabilities`，至少包含 `assistant_message_phase: Literal["native", "unavailable"]`，缺省 unavailable。下游调用类型化 `get_model_capabilities(model: LanguageModel)` 在请求前查询；真实 `LLMClient.capabilities` 从绑定 spec 构造，旧实现缺少属性或返回非项目能力类型时保守返回 unavailable。LanguageModel 保持原有必需方法，不强迫旧 fake/eval 包装同步新增属性。模型切换时能力随新实例更新。能力类型不包含 Provider 名、API 名、SDK 类型或 endpoint。

`LLMTextDelta` 增加 `phase: Literal["commentary", "final_answer"] | None = None`，追加在现有字段后，保持旧构造方式。Chat 文本始终 None。

115 应用能力声明决定是否具备原生阶段语义，再按每个事件的真实 phase 处理；native 不保证每轮出现两个阶段。若下游另需原生消息边界或生命周期，应在它自己的契约中明确，114 不把“用了 Responses”直接包装成“完整新交互已经可用”。

`DeferredLanguageModel.capabilities` 通过 `get_model_capabilities(self._get())` 纯转发；不会调用 generate/stream，不产生 LLM 请求，也不加入 Provider/API 分支。它可能像现有 context_window 一样触发延迟实例装配。用户已确认此最小越界及可选查询兼容方案。

## 原生 transport 与证明

复用锁定的 OpenAI SDK 2.24.0，直接调用 `AsyncOpenAI.responses.with_raw_response.create(..., stream=True)`；SDK `max_retries=0`。不通过 LiteLLM 的 Responses compatibility bridge。

Responses transport 复用现有 `CancellableAsyncStream`：后台 async source 拥有 HTTP client 与流，主线程暴露同步 Iterator。响应头等待与事件等待都轮询项目 cancel_check，保留既有 idle timeout。没有 cancel_check 时也要保证 generator.close 能确定性释放资源。

DeepSeek base 为 `https://api.deepseek.com`，Kimi 为 `https://api.moonshot.cn/v1`；wire model 明确来自 spec，而不是上层裁切字符串。

HTTP mock 必须在 SDK 实际发送边界断言 method、host/path、JSON body，确认恰为 `/responses` 或 `/v1/responses`。将 Chat completion 函数设为调用即失败，证明无客户端回退。真实 smoke 只保留 endpoint、请求指纹、模型和事件统计。

客户端主动关闭连接不能保证 Provider 已经开始的计算绝对零计费；保证是停止本地消费、关闭连接且不续发/重放。对于 stateless Provider 不虚构服务端 cancel endpoint。

## 请求转换

转换发生在 `llm_providers/responses/request.py`，使用独立副本，不修改 caller-owned messages/tools/settings，也不走 DeepSeek 专用于 Chat 的 `extra_body.messages` 覆盖。

| 当前输入 | Responses 输入 |
| --- | --- |
| system/user 文本 | 对应角色的 message item；结构化内容使用 input_text |
| assistant 文本 | assistant message，内容转为 output_text；旧消息没有 phase 就不填 |
| assistant.tool_calls | 按顺序转为 function_call，保留 call_id/name/arguments |
| tool 消息 | function_call_output，保留 tool_call_id 到 call_id 的配对 |
| user text/image_url 内容块 | input_text/input_image；保留 URL/data URL 与受支持的 detail |
| DeepSeek 已上传图片 file_id | input_image.file_id；不可错当普通文件文本，也不重新上传 |
| Kimi 文件抽取出的文本 | 仍为 input_text；Kimi ms:// 图片引用仅在其 Responses 明确兼容且测试覆盖后开放 |
| function tool wrapper | 展平为 type/name/description/parameters/strict，保留 schema，不新增工具或提示词 |

默认值与 request.settings 的优先级保持既有语义。只转换明确支持的通用参数：max_tokens/max_completion_tokens 到 max_output_tokens，response_format 到 text.format，reasoning_effort 到对应 Responses reasoning 参数。冲突别名、结构错误和不支持的配置在发送前抛项目 BadRequest；不以丢字段或改 Prompt 实现兼容。

tool_choice 必须依目标 Responses 能力校验。例如 K3 Chat 文档列有 required，Responses 页面目前只列 auto，不能直接照搬。真实首轮 400 的根因尚未确定，实施期应先保留脱敏错误 message 再定位；未经验证的 required/none 不进入 K3 正式 smoke。

内部 timeout、cancel、witness、diagnostic callback 不写入 JSON；stream_options 属于 Chat 配置，Responses 不发送。不允许 extra_body 覆盖 model/input/tools/stream 等已归一化核心字段。Provider 扩展只能通过本模块明确允许的参数规则转换。

DeepSeek thinking off/on 和 reasoning effort 必须在 Responses 中维持原有用户档位的语义；不能因为新 API 默认 thinking 开启就把原默认 off 静默变为 on。若厂商不支持等价参数，报告真实阻塞并回到方案确认，不通过 Chat fallback 掩盖。

## Output item 状态机

每次调用独立维护 response ID、output_index→item_id 映射与 item_id→状态映射。状态含 type、role、原生 phase、文本内容块、function call ID/name/arguments、是否 done。

1. `response.output_item.added` 登记 item。assistant 的 phase 仅接受 commentary/final_answer，缺失、null、未知值归为 None。重复 identity 冲突为协议错误。
2. `response.output_text.delta` 同时核对 item_id/output_index，确保属于 assistant message；从该 item 读取 phase 后输出 LLMTextDelta。不能使用最近一个 item 的全局 phase。
3. phase 在开始输出后不允许悄悄改变。终态与起始 metadata 冲突时报告协议错误，不能回写已发 delta 或按“最后一条”修正。
4. function_call item 获得独立稳定工具 index；首次发 ID/name，arguments.delta 发增量。arguments.done/output_item.done 只用于校验和补齐未发内容，不重复发送同一参数。多个工具严格按 item/call identity 分离。
5. reasoning 增量只产生节流的 LLMActivity；正文不输出到用户或 diagnostics。完整 reasoning item 按已有 provider-private state 通道保存必要回放信息。
6. content/item done 本身不是整轮完成。只有 response 的终态才结算 usage 和 ProviderAssistantTurn；同一流最多一个 LLMTurnDone。

终态快照与已观察 item 的文本/工具数据需要一致；未知 identity 的 delta、冲突 call_id、非法参数 JSON、重复矛盾终态、EOF 无终态都明确失败。不能靠 response.completed 中的最终数组顺序反推已发送文本的 phase。

## 工具与 reasoning 回放

completed 的 function items 归一化为现有 ProviderToolCall，LLMTurnDone.assistant_turn 继续提供合并文本与完整工具调用。Agent 仍按原逻辑执行工具，下一轮 Chat 风格 tool 消息由 converter 转为 function_call_output。

DeepSeek 的 reasoning 文本可以走既有 reasoning_content state；K3 的加密/结构化 reasoning items 走既有 opaque reasoning_details，并在 Responses converter 中恢复成独立 reasoning input items。不要把 SDK 对象存到上层；state 仍是 JSON-compatible，保留现有 model/replay_scope 隔离。切到 Chat generate 时不泄漏 Responses item 到不兼容参数。

当前 Agent 把一次 response 的多个 assistant message 合并到项目 Message，不能原样保留 phase 和全部 assistant item 边界。114 的 phase 输出与能力声明不等于已经实现多轮 phase 回放；这项用户明确排除的下游语义由 115 解决。不能滥用 reasoning_state 存整段 assistant 输出来绕过边界。

## Usage、结束与错误

增加独立 `extract_responses_usage()`：input_tokens 是包含 cache 的输入总数，output_tokens 是输出总数，total_tokens 保持上游事实。input_tokens_details.cached_tokens/cache_write_tokens 分别映射已有字段；缺失计数保持 None，不能把 inclusive input 再加 cache。

diagnostics 复用现有 response_id、response_model、provider_request_id、stream_open_ms、first_chunk_ms、witness 和内容无关 observation；transport 使用明确内部标识。请求转换改变 item 数量时，扩展域映射或降为 generic，不能把原消息索引冒充精确 output/input item 域。

| 上游结果 | 统一语义 |
| --- | --- |
| completed，有完整 function calls | LLMTurnDone(tool_use)，含 assistant_turn/usage |
| completed，有文本且无工具 | LLMTurnDone(end_turn) |
| completed，无正文且无工具 | 既有 LLMEmptyResponseError |
| incomplete，预算耗尽且无正文 | 既有 LLMOutputBudgetExhaustedError |
| 其它 incomplete，包括已有部分文本 | 项目 ProviderError 子类型携带结束原因与可用 usage/diagnostics；不发正常完成事件 |
| failed / error event / HTTP error | 现有 provider-aware 错误归一化；保留 safe code、状态码、请求 ID |
| Provider cancelled | 项目非成功终态，保留 cancelled 原因；不伪装 end_turn |
| cancel_check 抛出 / generator.close | 原取消传播，关闭 source/client；不转换成可重试 Provider 故障 |
| EOF 没有终态 | LLMIncompleteResponseError(missing_terminal)，明确失败、不重试、不产生成功终态；实际网络异常在已有文本后才归为 interrupted-after-output |

正常终态和错误终态分开；不为表达 max_tokens 而将被截断的未完成工具调用交给 Agent 执行。

## 重试边界

Chat 的当前重试边界保持不变。Responses 只沿用项目已有的同 API 有界瞬时错误重试：文本或 LLMTurnDone 已发出就不能重放；工具 delta 未终态时仍按现有 transient accumulator 契约重置。确定性参数错误、incomplete、协议错误、用户取消不重试。

没有跨 API fallback。即使尚无输出，也不能据此证明请求没有被处理或计费，因此不把“尚无文本”作为切换 Chat 的授权。

## OpenRouter 隐藏与兼容

ProviderDefinition 增加缺省为 true 的公开可见性；OpenRouter 设为 false 并注释暂不维护。公开模型 catalog 与公开模型列表过滤不可见 Provider，内部 catalog、模型识别、build_provider_spec、transport、错误/Key 验证均保留。

设置页隐藏 OpenRouter 凭据组并注明维护状态；保留前端/Rust Provider 枚举、runtime_config 与 Bridge 凭据注册表。无需设置迁移，不改变用户的历史会话、模型选择或 Keyring。OpenRouter 不参加本次 Responses 接入和真实 smoke，原 Chat 行为的 deterministic 回归继续执行。

用户知识按现有受管 Guide 流程维护，不手改 immutable builtin snapshot 或历史需求来伪造同步状态；合入时如涉及 Guide 发布授权门禁，单独遵守其工作流。

## 验证与实施约束

deterministic 测试包括：全 route 选择、真正 HTTP URL/body、所有文本 phase、缺失与未知 phase、多 item 交错、工具参数拼接、完成快照、usage inclusive/cache 分桶、错误映射、取消响应头等待/迭代/关闭、EOF、重试次数与禁止跨 API fallback。Chat 与 generate 请求仍由原测试固定。

K3 和 DeepSeek 的正式 smoke 使用公共 LanguageModel.stream，通过项目工厂绑定模型，执行固定合成工具循环；不使用用户会话、不提供外部有副作用工具。记录请求次数、使用模型、精确代码与 harness 身份、最终 endpoint、事件种类、phase 值及 usage。首次失败也留档，后续请求需继续匹配 llm-api-confirm 的有界授权。

设计期已消耗的四次原 SDK 探测不是正式产品 smoke；其中两个工具请求失败，不能计为工具通过。正式 smoke 前说明新的调用范围和 token 预算；不复用此前已用完的四次额度当作无限授权。

项目检查分别运行 `scripts/check/run.ps1 --stage backend`、`--stage frontend`、`--stage rust`。新增脚本提供 sh/ps1 两端。当前不启动桌面视觉验收；设置入口删除通过自动化验证，若用户需要真实桌面观察，再按相应门禁执行。

打包影响按实际 diff 判定：沿用已有 SDK、静态 Python 模块和普通设置清理预期 standard；若出现依赖闭包、资源装载或 frozen 高风险路径变化，按规则升档并执行对应验证，不以预期分类代替最终检查。

## 设计问题对照

| 用户问题 | 本方案回答 |
| --- | --- |
| 1–2 能力所有权与元数据来源 | 显式 catalog/spec；能力信息独立于 Provider/API 名；不依赖 LiteLLM 动态探测 |
| 3 如何证明原生请求 | 直接 SDK Responses + 最终 HTTP mock + 真实 endpoint 证据 |
| 4 请求转换 | 模块内 messages/tools/settings converter；受支持参数逐项校验 |
| 5–6 流关联与多 item phase | item_id/output_index 双向校验；phase 属于 item，不属于轮次 |
| 7 工具映射 | 原 LLMToolCallDelta/ProviderAssistantTurn/LLMTurnDone，不改 Agent 分支 |
| 8 phase 缺失 | None，静态 capability 不补值 |
| 9 失败回退 | 不跨 API 回退；同 API 重试维持明确的输出边界 |
| 10 generate 与 stream | 只有 stream 按能力切换；generate/complete 保留 Chat 单文本契约 |

## 变更记录

| 日期 | 内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-09-05 | 根据已确认方向、OpenRouter 移除与主力模型真实探测形成方案；标记工具 400 与最小能力代理越界项。 | 尚未开始生产实现 |
| 2026-09-05 | 用户授权实现，并把 OpenRouter 改为仅隐藏用户入口与注释暂不维护，保留旧代码和设置兼容。 | 按最终边界实施 |
| 2026-09-05 | 用户确认用 get_model_capabilities 查询可选能力，保留原 LanguageModel 必需协议；同时授权主力模型最多四次正式 smoke。 | 调整能力查询与 Deferred 转发，旧实现不扩改 |
| 2026-09-05 | 用户验收确认：私有路由以可选 ResponsesRoute 表达；EOF 不完整响应与实际网络中断分类分开。 | 同步已验证实现的最终口径，无生产代码修改 |
