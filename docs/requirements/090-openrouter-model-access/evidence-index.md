# OpenRouter 模型渠道接入与调用层解耦 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：OpenRouter 当前模型与 API 契约、同类产品交互、模型 SDK 抽象、本地代码现状及方案取舍。
- 本索引不记录：API Key、完整请求/响应内容、真实模型调用结果、实现进度或验收状态。
- 最近对账：2026-08-20，基线 `c6aba5cd92240d0e55de39865aac4eb68f99b128` 与当前已确认需求/方案一致。

## 当前综合判断

- OpenRouter 适合作为固定模型 allowlist 的调用渠道，不适合在本期直接暴露动态全量模型市场。
- Gemini 3.7 Flash 与 GPT-5.6 Luna 当前均有明确 OpenRouter slug、长上下文和 tools 能力；agent-friend 仍应按本地目录仅开放已验证的文本与工具能力。
- 成熟 SDK 普遍把产品/Agent 与 provider transport 分层，并使用类型化请求、流事件、错误、usage 和显式取消；不需要为了获得这层边界迁移整个 Agent loop。
- 当前项目已有项目流事件、错误、目录和 transport 雏形，最小正确演进是新增薄 `LanguageModel` 契约、下沉 provider 分支并继续把 LiteLLM 当作实现细节。
- OpenRouter 的 403 具有权限、guardrail 与 moderation 多种语义，不能沿用所有 403 都使凭据失效的通用分类。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-20 | 两款目标模型在 OpenRouter 的当前标识与能力 | 外部资料 | OpenRouter live `/api/v1/models`，采集于 2026-08-20 | 存在 `google/gemini-3.7-flash` 与 `openai/gpt-5.6-luna`；均声明 tools，context 分别为 1,048,576 与 1,050,000 | confirmed | active | https://openrouter.ai/api/v1/models |
| E-002 | 2026-08-20 | Luna 的官方模型身份与基础能力 | 外部资料 | OpenAI 官方模型目录，采集于 2026-08-20 | `gpt-5.6-luna` 面向成本敏感高吞吐，1.05M context，支持 Functions | confirmed | active | https://developers.openai.com/api/docs/models |
| E-003 | 2026-08-20 | OpenRouter Key 的无推理校验入口 | 外部资料 | OpenRouter API Keys reference | `GET /api/v1/key` 使用 Bearer Key，200/401/500 契约；响应账户字段无需进入产品 | confirmed | active | https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key |
| E-004 | 2026-08-20 | OpenRouter 路由、隐私与错误语义 | 外部资料 | OpenRouter provider routing 与 error reference | 支持 `require_parameters`、`data_collection=deny`；401/402/403/429/503 语义不同，typed error 与 availability 比 status 更精确 | confirmed | active | https://openrouter.ai/docs/guides/routing/provider-selection；https://openrouter.ai/docs/api/reference/errors-and-debugging |
| E-005 | 2026-08-20 | 聚合平台是否应直接暴露全量模型 | 外部资料 | Open WebUI OpenAI-compatible provider guide | OpenRouter 返回大量模型，官方文档强烈建议使用过滤 allowlist，否则目录拥挤且加载显著变慢 | strongly-supported | active | https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/ |
| E-006 | 2026-08-20 | 接口兼容能否推导模型能力 | 外部资料 | Jan custom endpoint guide | OpenAI/Anthropic wire 兼容不等于自动知道 tools、vision、audio；能力需要逐模型配置 | strongly-supported | active | https://www.jan.ai/docs/desktop/remote-models/custom-endpoint |
| E-007 | 2026-08-20 | AI SDK 中值得借鉴的模型层边界 | 外部资料 | Vercel AI SDK 6 provider management、streamText、error handling | provider registry 与模型别名独立于调用方；full stream 使用类型化事件；AbortSignal、error/abort/tool-error 和 provider metadata 有明确边界 | strongly-supported | active | https://ai-sdk.dev/docs/ai-sdk-core/provider-management；https://ai-sdk.dev/docs/reference/ai-sdk-core/stream-text |
| E-008 | 2026-08-20 | Python 模型框架如何拆 Provider/Model/Profile | 外部资料 | PydanticAI v1.97 model/provider docs | Provider 管鉴权连接、Model 管统一请求、Profile 管模型能力和限制；不支持的关键能力在请求前 fail-fast | strongly-supported | active | https://ai.pydantic.dev/models/overview/ |
| E-009 | 2026-08-20 | OpenAI Agent SDK 对模型与 transport 的建议边界 | 外部资料 | OpenAI Models and providers guide | 生产显式选择模型；provider/transport override 只在需要时使用；model settings 与 Agent 定义/工作流选择分开 | strongly-supported | active | https://developers.openai.com/api/docs/guides/agents/models |
| E-010 | 2026-08-20 | LiteLLM 是否仍适合作为 OpenRouter 传输层 | 外部资料 | LiteLLM 1.83.x docs/source + 当前锁版本 1.83.14 | 支持 `openrouter/<model>`、stream、tools 和 `extra_body`；适合做 Python 兼容适配，但不应拥有产品目录或 Agent loop | strongly-supported | active | https://docs.litellm.ai/；`../../../uv.lock` |
| E-011 | 2026-08-20 | 本地模型层当前是否已经真正解耦 | 代码 | `c6aba5c`，`client.py` 超过千行；`conversation.py` concrete imports/type check | 已有项目事件和 transport，但 Conversation 仍读取 `spec`、判断 `LLMClient`，Bridge/Memory/test 直接依赖具体类 | confirmed | active | `../../../llm_providers/src/llm_providers/client.py`；`../../../agent/src/agent/conversation.py`；`../../../agent_bridge/src/agent_bridge/runtime_config.py` |
| E-012 | 2026-08-20 | 089 是否能承载 OpenRouter 凭据 | 代码 | `c6aba5c` runtime credential coordinator | group registry、candidate staging、provider validation、revision、Bridge restart 和 runtime auth failure 均可按新增 group 扩展 | confirmed | active | `../089-runtime-credential-validation-and-apply-feedback/design.md`；`../../../frontend/src-tauri/src/runtime_credentials/` |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
| --- | --- | --- | --- |
| LiteLLM 1.83.14 对 OpenRouter mid-stream error 是抛异常还是透传 chunk | 两种版本/路径可能不同 | 使用构造的 fake chunk 与 mock transport 覆盖两种形态，核对当前版本源码行为 | 不阻塞；设计要求两种都归一化 |
| 两款模型在真实 OpenRouter route 下是否完整支持 agent-friend 工具循环 | 静态目录声明支持 tools，但实际 endpoint/参数组合仍可能差异 | 实现后按授权各执行一次最小真实流式工具调用 | 不阻塞自动化实现；阻塞真实 AC 观察结论 |
| `data_collection=deny` 在特定地区/账户是否筛掉全部 endpoint | 当前在线目录不能代表用户实际路由 | mock availability 固定错误语义；真实验证时观察 route result | 不阻塞；必须 fail-closed |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| OpenRouter 采用固定两模型 allowlist | E-001、E-005、E-006 | 当前 | 用户明确要求模型市场，且能解决能力治理与目录性能 |
| 保留 LiteLLM 作为薄传输实现 | E-007、E-008、E-009、E-010、E-011 | 当前 | 关键 provider 长期无法可靠适配，或 Agent runtime 整体迁移到 Node/TS |
| 不迁移 AI SDK / PydanticAI Agent loop | E-007、E-008、E-009、E-011 | 当前 | 产品明确决定替换现有工具、审批、事件与持久化语义 |
| 新增项目自有 LanguageModel 契约 | E-007、E-008、E-011 | 当前 | 本地代码证明已有等价边界且能移除 concrete dependency；当前证据相反 |
| 只有明确 authentication 使 OpenRouter 凭据失效 | E-003、E-004、E-012 | 当前 | OpenRouter 官方稳定错误契约发生变化 |
| 允许同一模型的 provider endpoint fallback，禁止跨模型 fallback | E-004、E-009 | 当前 | 产品明确引入用户可见 fallback 策略与能力/成本约束 |
| OpenRouter 首期只开放文本与工具 | E-001、E-006、E-011 | 当前 | 新增独立多模态适配与真实验证需求 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
| --- | --- | --- | --- | --- |
| E-001–E-010 | 对应官方公开 URL | 未归档，索引保留 URL 与采集日期 | 未保存网页快照；内容可能随版本变化 | 公开，无凭据 |
| E-011–E-012 | 仓库 `c6aba5c` | 当前 Git 历史与相对路径 | commit SHA 锚定 | 私有仓库代码，不含 secret 内容 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
| --- | --- | --- |
| 2026-08-20 | requirement、design、OpenRouter/OpenAI 当前目录、SDK 官方文档、本地 `c6aba5c` | 一致；确认外部只承担模型传输与供应，Agent 产品语义保持项目自有 |
