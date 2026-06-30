# 002 · 工具使用契约与工具描述要分层

- **状态**：active
- **来源日期**：2026-06-30
- **来源**：requirement 039 / issue 018 replay / system prompt exploration
- **适用范围**：system prompt、tool description、工具结果渲染、外部动作工具设计
- **最近更新**：2026-06-30
- **替代条目**：无

## 经验

Agent 的工具行为不能只靠 tool description，也不应该把所有工具细则都塞进运行时上下文。更稳定的结构是分层：通用 system prompt 约束“如何真实地陈述工具动作和证据来源”，每个 tool description 说明“这个工具何时用、何时不用、参数从哪里来、结果边界是什么”，工具结果则在最靠近证据的位置提醒“哪些内容被结果支持”。

这三层职责不同：通用契约管跨工具红线，工具描述管单工具选择，工具结果管本次返回证据。任何一层缺失，都容易让模型在自然语言里把“当前上下文”“印象”“用户暗示”说成自己查过、翻过或工具返回过。

## 背景

039 的 issue 018 replay 一开始看似是 memory inconsistency 问题：Echo 被追问“你刚才不是不记得吗”时暴露“没存下 / 小本本”等存储隐喻。后续真实 LLM replay 又暴露出更通用的坏样本：模型没有实际调用历史工具，却说自己“翻了一下之前的聊天记录”。这说明问题不只是 memory 文案，而是工具动作陈述缺少统一证据边界。

排查现有 prompt 后发现，工具相关规则散在 `project_identity.md`、`runtime_context.md`、单个工具 description 和工具 result reminder 里；同时 `Tool.description` 协议注释还把描述定位成“一句话”。这和主流 coding agent / tool-use API 的实践不一致：工具描述需要足够详细，但通用的“不要虚构工具动作 / 不要暴露工具机制”应由 system prompt 层统一承担。

## 反模式

- 在具体 bad case 里禁止“翻聊天记录”这类自然说法，导致真调用工具时也不敢自然表达。
- 只在某个 memory prompt 里修“别说查记录”，但其他工具仍可出现未调用却声称调用的同类问题。
- 把 `runtime_context.md` 变成所有工具细则的大杂烩，导致时间 / 新鲜度 / 历史归因 / 工具失败都抢同一块职责。
- tool description 只有一句“搜索互联网”或“查询历史”，没有写禁用场景、参数来源、结果边界和失败处理。
- 工具结果 reminder 诱导模型复述技术动作，或没有说明本次结果不支持哪些结论。

## 推荐做法

- 新增跨工具 prompt section，集中约束：只有实际调用过才可说查过 / 翻过；不要暴露工具名、参数、schema、provider、SDK、数据库、日志、缓存、trace；工具失败用自然话术兜底；外部写动作先确认。
- 每个 tool description 至少覆盖：用途、何时使用、何时不要使用、参数来源与证据边界、失败或空结果怎么处理、是否需要用户确认、是否允许重试或连续查询。
- `runtime_context.md` 只保留运行时事实：当前时间、近期信息、事实证据、历史归因边界。不要继续承载每个工具的详细使用说明。
- 工具结果文本应在最靠近证据的位置提醒边界：哪些字段来自结果、哪些没有返回、哪些问题不能用这个工具证明。
- 测试不仅断言工具能被调用，也要断言最终回复没有“无证据工具动作声明”，例如没调用历史工具却说“我翻了记录”。

## 适用与不适用

适用：

- 新增或修改任何会暴露给 LLM 的工具。
- 调整 system prompt 中关于搜索、历史召回、外部动作、工具失败的话术。
- 排查模型“没查却说查了”“工具失败暴露 API / provider”“把搜索当历史证据”等问题。

不适用：

- 纯内部 helper、不会进入 LLM tools 数组的模块。
- 只影响开发者日志或观测 meta、不会回喂给 LLM 的结构。
- 单纯的 UI 渲染文案，除非它会影响 LLM 可见的工具结果文本。

## 证据与关联

- 关联需求：[039-memory-inconsistency-response](../../requirements/039-memory-inconsistency-response/requirement.md)
- 关联 issue：[018-agent-exposes-storage-metaphor-when-confronted](../../issues/018-agent-exposes-storage-metaphor-when-confronted/README.md)
- 关联探索：[system-prompt-lessons-from-fable-5](../../explorations/system-prompt-lessons-from-fable-5/README.md)
- 关联实现：`agent/src/agent/prompt_sections/tool_usage_contract.md`
- 关联实现：`agent/src/agent/tools/protocol.py`
- 关联实现：`agent/src/agent/tools/builtin/conversation_history/tool.py`
- 关联实现：`agent/src/agent/tools/builtin/web_search/tool.py`
