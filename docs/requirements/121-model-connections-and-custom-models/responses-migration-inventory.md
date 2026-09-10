# Responses 迁移盘点（需求澄清材料）

## 范围与证据边界
- 日期：2026-09-09；源码 HEAD：`8cf97e931cf47188522ff3cfc44682a1eaf369fa`。
- 相关后端代码无工作区修改；第一步前端 overlay 不改变这些调用路径。
- 依赖实装：OpenAI Python 2.24.0、LiteLLM 1.83.14。
- 本次只有源码/文档核对、合成输入复现和离线回归，没有真实模型调用。
- 本文描述调查结果和待验证条件，不确认迁移方案；索引见 [evidence-index.md](evidence-index.md)。

## 回答当前问题
Responses 协议支持 JSON mode 和 JSON Schema，assistant phase 作为 item 元数据不直接改变 JSON 文本。
本项目可以研究将 Responses-capable route 的 generate/complete 也迁移到 Responses，但当前实现不能直接切换。
移除整个 Chat Completions 适配层是另一个兼容性决定，目前既有 Kimi K2.x、隐藏 OpenRouter 与未知 BYOK 服务都不能据现有证据统一迁移。

## 历史决定
114 的 requirement AC-14 和 design“现状与调用链”明确保留 generate/complete 的 Chat 路径，并指出调用方包括上下文摘要、记忆抽取。
这是当时的范围隔离，现有持久化记录没有证明 Responses 无法输出 JSON；用户在本任务补充曾担心分阶段输出影响记忆。
114 的流式文本、工具闭环和 DeepSeek thinking-off 真实 smoke 是历史事实，不能当作非流式 JSON、记忆质量或所有型号的验收。

## 官方资料核对
| 来源 | 直接事实 | 推断边界 |
|---|---|---|
| [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) | Responses 支持 JSON mode、JSON Schema，参数位于 text.format；需要处理 refusal 和 incomplete | JSON 能力受模型支持及 schema 子集限制，不等于第三方兼容 |
| [OpenAI phase 指引](https://developers.openai.com/api/docs/guides/deployment-checklist#set-up-the-assistant-phase-parameter) | phase 标识 commentary/final_answer，历史回放应保留 | 不保证每个 provider 或每个 item 都有 phase，也不是输出格式开关 |
| [OpenAI 迁移指引](https://developers.openai.com/api/docs/guides/migrate-to-responses) | 参数、tool shape、默认 strict 和服务端存储语义存在差异；支持 store=false | 不能只改 URL；SDK output_text 是聚合 helper，不是 phase 选择器 |
| [DeepSeek Responses](https://api-docs.deepseek.com/api/create-response/) | 支持非流式、text/json_object/json_schema；reasoning.effort=none 关闭思考；max_output_tokens 包括 reasoning；接口无状态 | 已有 Flash 流式测试不证明当前机器输出及所有模型质量 |
| [Kimi OpenAPI](https://platform.kimi.com/docs/openapi.json) | /v1/responses 当前 model 描述为 kimi-k3；stream 默认 false；text.format 仅列 json_schema；effort 为 low/high/max | 不从 Chat 参数或同系列名称推断 K2.x、json_object、temperature 支持 |
| [Codex WireApi](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs) | 当前公开源码 WireApi 仅 Responses，chat 配置明确报不再支持 | 只证明该模型 wire 边界；不证明 Codex 每项后台能力都调用同一端点，也不证明我们的供应商均支持 |

远端来源是当日获取的可变官方页面/main，未锁定远端 commit，不将其冒充 immutable source。Kimi 网页 find 失败后使用其 OpenAPI request schema 核对参数。

## 生产调用入口清单
搜索覆盖 agent、memory、agent_bridge、voice_bridge、llm_providers、runtime_config 的 generate/complete/stream、ModelRequest、SDK 与 response_format 调用；排除了 HTTP 文件流、数据库 complete 等同名非 LLM 操作。

| 入口 / 源码锚点 | 当前协议 / 输出契约 | 迁移必须核对 |
|---|---|---|
| agent/conversation.py:3602，LanguageModel.stream | 按 route：Responses 或 Chat；事件、工具、phase、usage | 多 item、工具回传、reasoning 回放、取消、重试、已有会话恢复 |
| memory/extraction/extractor.py:130，基础抽取 generate | Chat；提示词要求 JSON，未设置 response_format；随后 json.loads | 最终正文选择、空/非法 JSON、Core/Semantic/Episodic 形状、无效结果不落库 |
| 同文件 :223，语义协调 generate | Chat；另一个 JSON prompt/解析器，失败回退基础抽取 | 独立覆盖；不能仅验证基础抽取后就认为记忆迁移完成 |
| agent/context/summarizing.py:314，压缩 generate | Chat；max_tokens；提示词要求 analysis/summary 标签，strip_analysis 取摘要 | 并非 JSON；标签保真、输出预算、context 错误重试、取消及摘要落盘 |
| agent_bridge/notifications/generation.py:158，通知 stream | 已按 route 走 Responses/Chat；拼接所有 LLMTextDelta，限制 160 字符并检查 end_turn | 当前不按 phase 分流；commentary 可能进入通知正文或触发超长降级，需纳入机器最终结果盘点 |
| llm_providers/client.py:350，兼容 complete | 转 generate，只返回 text | 不能留成另一套协议选择和返回值语义 |
| agent_bridge/runtime_config.py:78，DeferredLanguageModel | 纯转发 generate/stream，延迟凭据解析 | 维持启动不依赖凭据、控制信号和能力转发 |

入口路径表中的模块路径相对各自 src/ 根。Desktop AG-UI、IM、scheduled tasks、语音主对话经 Conversation 汇合，仍需入口级回归，不能重复算成独立 SDK 调用点。
语音 `voice_bridge/routes/llm_proxy.py:33` 的 `/v1/chat/completions` 是火山 RTC 到本机 Bridge 的兼容接口。迁移向厂商发出的请求不要求改变该入站接口；误改会破坏 RTC 对接。

## 评测与操作入口
- `memory_eval/src/memory_eval/__main__.py:116` 使用 ProviderSpec.from_config，未绑定 catalog ResponsesRoute；只改产品工厂可能仍然评测 Chat。
- `agent_eval/src/agent_eval/runner.py:_build_manager` 使用传入 spec 构造 LLMClient；需核对实际选择与 HTTP witness，不能只看模型名。
- `memory_eval/harness/runner.py:_NoCallLLM` 是禁止调用的检索回放桩，不是迁移遗漏。
- `scripts/responses-smoke` 既有测试聚焦 stream/工具；`context-compaction-smoke`、`prompt-cache-smoke`、`notification-smoke`、social-platform agent smoke 的包装及配置要随各自调用路径核对。
- 原测试 `llm_providers/tests/test_responses.py:test_generate_and_legacy_stream_stay_chat` 明确锁定旧行为，迁移需有意修订这一契约，不能绕过测试。

## 已复现的文本聚合风险
现有 ResponsesState.finish 保留 text_items 及其 phase，但 assistant_turn.content 是所有 item 文本拼接。
实装 OpenAI SDK Response.output_text 同样聚合所有 message 的 output_text，源码没有 phase 过滤。
合成一个 completed response，依次包含 commentary `I will extract the memory.` 和 final_answer `{"entries": []}`：
- item phase 均保留；
- 汇总文本为 `I will extract the memory.{"entries": []}`；
- 将汇总文本交给现有 `_parse_output_with_status`，得到 `invalid_json`。
结果见 [artifact](artifacts/responses-text-aggregation.json)。这是组合机制复现，不是现有生产 generate 的 bug（它仍走 Chat），也不证明 structured-output 请求会实际返回该组合。
复现使用当前 ResponsesState.feed(response.completed)、finish 与 memory parser；未启动 SDK client 或发送请求。前两次临时命令因误用 helper 字段/方法名失败，未产生有效结果；artifact 只记录修正后的成功复现。

## 迁移缺口清单
以下是调查得到的验证/需求缺口，不是已确认的实现方案。

1. 请求模式：prepare_request 固定 stream=True，ResponsesSource 按异步事件迭代；没有完整的非流式 Responses→ModelResponse 通路。
2. 机器最终结果：ModelResponse 只有 text/usage/diagnostics；需明确多 final item、缺 phase、只有 commentary、拒答、截断、意外工具调用的行为。不能靠“取最后一段”或任意拼接隐式决定。
3. JSON 强度：当前记忆只用 prompt+parser。启用 json_schema/strict 会改变模型约束与可接受 schema，需要与协议迁移分开归因；JSON 语法合法也不能证明记忆事实正确。
4. 参数：max_tokens→max_output_tokens、reasoning 默认值、temperature、tool_choice、parallel_tool_calls 和 text.format 按实际 route 核对。DeepSeek/Kimi 不是完整 OpenAI 请求 schema。
5. 终态：现有 stream 有 completed/incomplete/refusal 等处理；新 generate 要保持相同错误分类、usage 与取消语义。部分文本和可解析但未完成的 JSON 不能直接当成功。
6. 重试与诊断：generate 的部分错误/缺省诊断仍标 litellm；cache witness、transport、usage、reasoning tokens、重试计数和取消必须随新通路核对。
7. 存储语义：当前 request builder 拒绝显式 store=True，但未给通用 OpenAI route 默认设置 store=False；OpenAI Responses 默认保存响应。BYOK 通用 OpenAI 接入要明确数据存储行为，不能将 DeepSeek 无状态默认外推。
8. 工具 strict：当前 convert_tools 保留原 schema/strict 的有无；OpenAI 两个协议省略 strict 的默认行为不同。完整迁移需确认 optional 字段语义不改变。
9. 回放：reasoning items、assistant phase、call ID 与工具结果匹配应保持；不能因为非流式输出只消费最终文本，就删掉交互流的原生 item 元数据。
10. 供应商范围：K2.x 与隐藏旧配置仍需 Chat；BYOK 明确选 Responses 的连接应验证纯 Responses 后台调用，不能偷偷改发 Chat 或别的连接。

## 当前验证与未验证
离线回归命令：`scripts/test/run.ps1 -q llm_providers/tests/test_responses.py memory/tests/test_extraction.py memory/tests/test_extractor_pass1.py agent/tests/test_context_summarizing.py agent_bridge/tests/test_notifications.py`。
结果：177 passed（84.09 秒）。这证明当前相关契约在本环境通过，不证明尚未实现的迁移通过。

后续有区分力的验收证据：
- HTTP mock：两种调用方式实际 URL/body、无隐式 Chat 回退、格式映射和参数拒绝。
- 合成 output item：commentary/final/无 phase/多 final/拒答/截断/工具项及 usage/cancel/retry。
- 产品行为：基础记忆、语义协调、摘要、通知、工具续接，以及保存/恢复、CLI/eval 路由身份。
- 独立真实验证：每个受支持 provider 使用同一组脱敏夹具核对 JSON 解析、领域校验、记忆行为、摘要完整性、时延和 token；需有界授权后才运行。
- 发布判断：上述验证和用户验收完成前，不能宣称“可直接全量迁移”或“质量不变”。

连接修改何时生效、删除历史引用及默认连接删除限制的同类产品调研仍待继续；本报告不替代该部分需求澄清。
