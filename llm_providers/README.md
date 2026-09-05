# llm_providers

`agent-friend` 的 **LLM Provider 适配层**。

## 定位

项目里**唯一**与 LLM API 直接通信的模块，对所有上层（agent、memory）暴露统一接口。

职责严格收敛在"打 LLM 这通电话"：
- 接收 messages，返回 completion（含流式）
- 抹平不同 Provider 的接口差异
- 集中处理 API key、超时、重试等基础设施级别的事

**禁止**做的事：
- 多轮对话编排（属于 `agent`）
- 记忆召回 / 拼装（属于 `agent` + `memory`）
- 工具调用编排（属于 `agent`）
- 任何业务逻辑

> 这一边界在 [`docs/decisions/0002-incubation-tech-stack/README.md`](../docs/decisions/0002-incubation-tech-stack/README.md) 第 3.14 节被显式强调："LiteLLM 只做'打 LLM 这通电话'，所有 agent 编排都在 `agent/core/` 自己实现"。

## 公共接口与 transport

上层使用 `LanguageModel`、`ModelRequest`、`ModelResponse` 与 `LLMStreamEvent`，不读取 Provider SDK 对象，也不判断 Responses / Chat Completions。工厂 `create_language_model()` 负责绑定 catalog/spec。

- `generate()` 与兼容入口 `complete()` 保持 Chat Completions 单文本直出。
- `stream()` 仅对显式配置 `ResponsesRoute` 的模型使用 OpenAI SDK 原生 Responses；没有声明的模型保持现有 Chat transport（LiteLLM / Kimi direct）。
- 原生 Responses 不经过 LiteLLM 的兼容桥，不允许失败后切换 Chat。SDK 自动重试关闭，由项目现有同 API 有界重试层处理瞬时故障。
- 文本事件的 `phase` 为 `commentary`、`final_answer` 或 `None`，只来自对应 assistant output item；Chat 始终为 `None`，不能根据工具或消息位置补值。
- 原生文本通过 `LLMAssistantItemStart` / `LLMAssistantItemDone` 和 delta 的 `item_id`、`output_index` 保留身份与顺序。完成结果的 `ProviderAssistantTurn.text_items` 保留各段，手动回放使用 `to_openai_messages()`；单数方法仅保留旧的聚合入口。
- phase 是可更新的生命周期元数据：item 完成事件提供当前 phase，完整 response 的 `text_items` 提供最终 phase；不要求开始/完成值不变。身份、正文与工具参数仍单独校验，阶段校正不重发正文；缺失/非法完成值为 `None`，不沿用开始值猜测。
- Chat 请求边界移除 phase 和内部 response 分组字段，并把同一响应的多个 assistant item 合并为合法工具事务；原始模型历史不被修改。
- 下游以 `get_model_capabilities(model).assistant_message_phase` 查询 `native` / `unavailable`；旧 LanguageModel 实现无需新增属性，缺失时保守返回 unavailable。静态 native 不保证每个 item 都有 phase，也不保证一轮出现两个阶段。
- 工具调用仍通过 `LLMToolCallDelta` 与 `LLMTurnDone.assistant_turn` 交付。取消、错误、usage 与诊断继续归一化到项目类型。
- `response.incomplete`、失败、取消和缺失终态均不发成功 `LLMTurnDone`，避免执行未完成的工具调用。

当前原生 Responses route 为 DeepSeek V4 Flash / Pro / Flash Vision 和 Kimi K3。Kimi K2.x 保持 Chat；既有默认模型不变。接口能力与原生 phase 的正面证据分开维护，不能由同族模型推断。

115 按确认范围开启 DeepSeek 三个现有模型的 native capability，K3 保持 unavailable；这是产品启用范围，不等于三个模型都完成了本期真实验证。逐 run 语义校验与 UI 资格来自 Agent 产品事件，具体状态见 [115 进度](../docs/requirements/115-assistant-phase-execution-disclosure/progress.md)。

OpenRouter **暂不维护、公开模型与凭据入口隐藏**，内部 catalog、凭据校验、Chat transport 与存量设置兼容仍保留，不自动删除用户配置。

实现方案、逐模型证据及验收状态见 [需求 114](../docs/requirements/114-llm-provider-responses-api/design.md) 与 [实施进度](../docs/requirements/114-llm-provider-responses-api/progress.md)。本模块不实现需求 115 的阶段编排、持久化或折叠 UI。

## 验证

单测使用 fake / HTTPX MockTransport 固定原生 endpoint、请求转换、item/phase 关联、工具、usage、终态与取消。可用 `scripts/test/no_external_http.py` 的 opt-in pytest 护栏防止过期 mock 意外发出真实 HTTPX 请求，调用方式见 [scripts](../scripts/README.md)。

`scripts/responses-smoke/run.*` 是独立的主力 Provider 公共接口 smoke，必须单次授权并传 `--allow-llm`；每 Provider 最多两个合成请求，不执行外部副作用工具、不读取用户会话、不自动重试。

## 与其他模块的依赖

- 依赖 `shared`（跨模块共享类型）
- 被 `agent`、`memory` 与运行时装配调用方依赖
