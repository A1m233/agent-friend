# 114 · Responses API 证据索引

## 用途与范围

归属：[需求](requirement.md)。记录官方接口、锁定依赖、调用链和有界真实探测；不记录凭据、用户会话、推理正文或 UI 推断。最近对账：2026-09-05。

## 当前综合判断

- DeepSeek V4 Flash 的正式 `/responses` 已在一次真实流中返回 assistant item 原生 `phase="final_answer"`。先前仅按文档缺少 phase 把它标为 unavailable 的结论被实际结果推翻。
- Kimi K3 的正式 `/v1/responses` 已在一次真实流中成功返回文本和 usage；该样本 assistant phase 为 `None`。这不证明模型永不返回 phase。
- 两个 Provider 的设计期首轮工具探测均 HTTP 400；未记录错误 message，无法确定根因。后续公共接口 smoke 已证实两家工具闭环，但 DeepSeek 关闭思考的参数转换仍需修正后复验（E-012/E-013）。
- DeepSeek V4 Pro、Vision Exp 的 Responses 支持有官方模型级证据，本次尚未发真实请求。不能把 Flash 的 phase 观测外推给另外两个模型。
- Kimi 官方 Responses 当前仅列出 K3；既有四个 K2.x route 没有相同证据。OpenRouter 最终范围是隐藏公开入口、保留兼容代码并注明暂不维护（E-011 覆盖早先删除方向）。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 / 关键锚点 | 摘要与局限 | 置信度 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-09-05 | DeepSeek 现有模型支持 Responses | 官方 [API schema](https://api-docs.deepseek.com/api/create-response/)、[模型表](https://api-docs.deepseek.com/quick_start/pricing/)、[变更日志](https://api-docs.deepseek.com/updates/) | 三个模型均列为支持；Context7 返回过旧的 Flash-only 内容，已由当前官方页与 2026-08-13/21 发布记录 supersede。 | strongly-supported | active |
| E-002 | 2026-09-05 | Kimi Responses 模型范围 | 官方 [Responses API](https://platform.kimi.com/docs/api/responses)、[模型参数](https://platform.kimi.com/docs/api/models-overview)、[OpenAPI](https://platform.kimi.com/docs/openapi.json) | 当前端点模型为 K3；Chat 的工具选择与参数约束不能直接外推到 Responses，Responses 页面目前只列 `tool_choice=auto`。 | strongly-supported | active |
| E-003 | 2026-09-05 | Responses 与原生 phase 是独立能力 | 官方 [OpenAI API reference](https://developers.openai.com/api/reference/python/resources/responses)、[GPT-5.5 phase guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5) | assistant phase 源于 output item；手工回放需保留。接口支持本身不能证明某模型返回两个 phase 值。 | confirmed | active |
| E-004 | 2026-09-05 | LiteLLM 可能在本地桥接 Chat | `uv.lock`: LiteLLM 1.83.14；[锁定版本源码](https://github.com/BerriAI/litellm/blob/v1.83.14-stable/litellm/responses/main.py) | 未取得 Provider Responses config 时进入 Chat bridge；不能只断言调用了 aresponses。原生传输拟直接使用已锁定 OpenAI SDK 2.24.0。 | code-supported | active |
| E-005 | 2026-09-05 | 初始 phase 静态判断 | 前序对话，基于 DeepSeek/Kimi 文档未出现 phase | 将“未找到文档”提前定为 unavailable；对 DeepSeek Flash 已由 E-007 推翻。Kimi 仅能保留“尚无正面证据”。 | unconfirmed | invalidated |
| E-006 | 2026-09-05 | 工具探测失败 | 2026-09-04T19:21:06Z，OpenAI SDK 2.24.0，base `1c2decdaf62de477a66f0ab9ebf0114605e5bd59`，无生产代码 overlay | DeepSeek Flash / Kimi K3 各 1 次，均 400 invalid_request_error；没有流、usage 或工具续接。缺少错误 message，根因未定。 | confirmed（仅失败事实） | active |
| E-007 | 2026-09-05 | Flash 原生 phase | 2026-09-04T19:21:58Z，最小请求，`POST api.deepseek.com/responses` | added/done assistant item 均为 final_answer，completed，112 tokens；未观测 commentary，不代表每轮均有 phase。 | confirmed（单次观测） | active |
| E-008 | 2026-09-05 | K3 原生流 | 同批，`POST api.moonshot.cn/v1/responses` | completed，158 tokens，assistant phase None；不能据一个样本判为永不支持。 | confirmed（单次观测） | active |
| E-009 | 2026-09-05 | OpenRouter 早先移除方向 | 前序用户指示；base 上 catalog、凭据与 Provider 枚举 | 删除枚举会影响旧设置反序列化；最终改为隐藏入口、保留代码。 | confirmed（历史方向） | superseded by E-011 |
| E-010 | 2026-09-05 | 公共能力属性的上层兼容 | `agent_bridge/runtime_config.py:DeferredLanguageModel`，`assembly.py` 将其用于 Memory | 用户已确认改用 get_model_capabilities 查询；已保留原 LanguageModel 必需协议，纯能力转发与 fallback 测试通过，611 文件 mypy 无错误。 | code-supported | active |
| E-011 | 2026-09-05 | OpenRouter 最终隐藏边界 | 用户明确要求“先不动代码，先不对用户暴露，注明暂不维护，然后开始实现” | 公开 catalog 与设置凭据组隐藏；内部定义、transport、枚举、校验和存量配置保留，无迁移与删除。公开 HTTP catalog 断言与设置组件 SSR 测试覆盖入口。 | confirmed | active |
| E-012 | 2026-09-05 | 公共 LanguageModel 工具闭环 | `artifacts/public-smoke-2026-09-05.json`；03:28:59–03:29:13 UTC，source digest 24c0841e1bc5c96235b56746660879495d01fef52b2b1b83fb1b13d5a4be373e | DeepSeek Flash 与 Kimi K3 各两次，四次成功；961+628=1589 tokens。Flash 正文 14 个 final_answer delta；K3 15 个 None delta。DeepSeek 这份报告的 passed 只断言工具闭环，不能证明请求标称 off 实际生效。 | confirmed（限定观测范围） | active / DeepSeek off 结论不可用 |
| E-013 | 2026-09-05 | DeepSeek Responses 思考关闭参数 | [官方 schema](https://api-docs.deepseek.com/api/create-response/) reasoning.effort、[兼容表](https://api-docs.deepseek.com/guides/responses_api/)；Context7 同步核对 | none 关闭；未设默认开启；不支持的参数静默忽略。初版错误地转发 Chat thinking.disabled，不能维持默认 off。已修正为 reasoning.none，补四档映射测试，并加强 smoke 的 native reasoning_tokens=0 / 无 reasoning state 断言；真实复验待新授权。 | confirmed（文档+代码） | active |
| E-014 | 2026-09-05 | K3 可选参数边界 | [官方 Responses](https://platform.kimi.com/docs/api/responses)、[OpenAPI](https://platform.kimi.com/docs/openapi.json)；实际 catalog / Conversation / voice 调用链 | 当前实际参数为 timeout、reasoning effort、session cache_key，可选 max_tokens。都在支持范围；对未列入请求规范的 temperature/top_p/parallel_tool_calls/metadata/store、无效 effort 与非 json_schema format 本地拒绝；不从响应同名字段推断请求支持。 | code-supported / official schema | active |
| E-015 | 2026-09-05 | DeepSeek 关闭思考真实复验 | `artifacts/deepseek-off-smoke-2026-09-05.json`，用户单独授权两次 | 两次原生 /responses 工具闭环均成功，748 tokens；两次 native reasoning_tokens=0，reasoning state=false；第二次正文 14 个 final_answer delta。修正后的默认 off 语义已验证。 | confirmed（两次调用） | active |

## 未决问题

| 问题 | 下一项证据 | 交付影响 |
| --- | --- | --- |
| 设计期首轮工具请求为什么 400 | 缺少原错误 message，无法回溯精确根因；不重放已用尽预算 | 只保留历史失败事实，E-012 已证明当前工具路径；不冒称初始失败根因已确认 |
| DeepSeek 默认 off 修正是否实际生效 | E-015 单独授权复验 | 已确认；不再是交付缺口 |
| Kimi 是否有原生 phase；DeepSeek 其它型号是否提供 phase | 正式多步骤 tool loop 的原生 item 观测，按具体型号记录 | 不伪造 phase；仅有正面证据的 route 声明 native |
| 转换是否保持工具与 reasoning 回放 | 公共接口 deterministic 回归 + 两个主力 Provider 的真实续接 | 原始 SDK 探测不能替代 |
| 115 如何回放分段 assistant phase | 115 的独立设计与实现 | 114 不修改 Agent 历史语义；能力字段不代表下游消费已经完成 |

## 决策影响

| 决定 | 证据 | 当前状态 |
| --- | --- | --- |
| DeepSeek 三个模型及 K3 建立原生 Responses transport | E-001、E-002、E-007、E-008 | 已确认方向；正式工具验证待完成 |
| Flash 的 phase capability 可以声明 native | E-007 | 更正先前静态判断；不外推其它型号 |
| OpenRouter 暂不维护，只隐藏公开入口 | E-011 | 用户明确确认；覆盖 E-009 的删除方向 |
| 用直接 SDK 请求证明无客户端 Chat bridge | E-004 | 已确认方向；必须用 HTTP mock 验证最终 URL/body |

## Artifact 映射

- 真实探测脱敏摘要：[artifacts/upstream-probe-2026-09-05.json](artifacts/upstream-probe-2026-09-05.json)。由本任务终端结果整理，非原始 SSE；完整原始 SSE 和推理正文未保存。
- 上述摘要文件 SHA256：`ab65e9946075338d1f3e6b9b529030f08f979a9bc0386f3ccd0dfc0eadb60beb`。
- 两批共实际 4 次请求；前两次失败，后两次成功。成功请求报告总 usage 270 tokens；失败请求没有 usage，不能据此断言无计费。
- 临时研究脚本位于本 worktree 被忽略的 `.tmp/114-native-responses-probe.py`；首次运行后增加了最小参数模式和脱敏错误 message，未重放首次请求。最终脚本 SHA256 `83a9fa885e5ea262d9e3d9c7f7293d761b0682830f0d1802917e9df6fdca78db`；它是后补测量脚本锚点，不是首批原脚本 hash。
- 命令依次为 `uv run --locked python .tmp/114-native-responses-probe.py` 与带 `--minimal` 的同一入口。均关闭 SDK 自动重试、使用系统凭据，不输出 Key。此临时入口是设计研究，正式可复验 smoke 将通过 scripts 双端入口运行。
- 没有读取历史用户会话、没有调用产品 Agent、没有调用 OpenRouter。
- 首次公共接口报告：[artifacts/public-smoke-2026-09-05.json](artifacts/public-smoke-2026-09-05.json)，SHA256 `fbd0fdaf51800e49faf6528458d1c4a6305b93ddb355cf6894bc8c98887f9daf`。保留原报告，不覆写 pass 标记；其断言覆盖范围与 DeepSeek off 缺口由 E-012/E-013 明确限定。
- DeepSeek off 复验报告：[artifacts/deepseek-off-smoke-2026-09-05.json](artifacts/deepseek-off-smoke-2026-09-05.json)，SHA256 `8a3227bda3150957f651e456f5fbf5d669a384f1fe587e6f2eb7a276a408422a`。本次单独授权两次全部成功，748 tokens；关闭思考与工具闭环以这份报告为准。

## 对账记录

| 日期 | 范围 | 结果 |
| --- | --- | --- |
| 2026-09-05 | 用户范围、官方资料、base 源码与四次探测 | 需求已修订；工具 smoke 未通过；phase 判断采用实际正面证据，不将样本缺失写为永久不支持。 |
| 2026-09-05 | Phase 3 实施 | 原生 HTTP mock 与状态机已实现；正式 smoke 入口和额度护栏已通过 fake 测试，未新增真实请求。OpenRouter 最终按仅隐藏实施。 |
| 2026-09-05 | 首次正式 smoke 与参数复核 | 四次已授权请求全部用完；两家工具闭环通过，发现并修复 DeepSeek off 转换遗漏；Kimi 当前正常路径无冲突，收紧未支持的可选参数。 |
| 2026-09-05 | DeepSeek off 修正后两次复验 | 单独授权两次，均通过；默认思考语义缺口关闭，等待用户验收。 |
