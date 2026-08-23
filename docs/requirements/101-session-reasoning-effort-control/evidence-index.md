# 101 · 会话推理强度控制 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：现有前后端能力、provider 推理参数与 continuation 契约、级联菜单组件能力、用户确认的需求边界，以及后续设计所依赖的版本锚点。
- 本索引不记录：真实 API key、完整 prompt、原始 reasoning 内容、用户会话正文或完整模型输出；真实 LLM smoke 只保留模型、调用链、脱敏 continuation 摘要和错误边界。
- 最近对账：2026-08-23，代码基线 `561d6e4068fd8b805f56ee2c298ddcada3414db8` 加当前需求实现 overlay；Tauri/bridge runtime 绑定同一 worktree 与 feature 分支。

## 当前综合判断

当前 overlay 已完成能力目录、会话原子配置、provider continuation 私有持久化与回放、级联交互和脱敏。真实工具循环 smoke 确认 DeepSeek V4 Flash、DeepSeek V4 Flash Vision 与 Kimi K2.6 都会在首个模型调用后执行 `calculate`，随后带 continuation 完成第二个模型调用；三条链路均记录 `model_continuations=1` 且无 provider 错误。Vision 首轮 smoke 曾因 LiteLLM 的 OpenAI-compatible 参数门禁失败；修复只在该精确 route 实际携带 `reasoning_effort` 时增加窄范围白名单，mock HTTP 与源码新鲜 runtime 的真实复验均确认参数保留、replay 成功且公开投影继续脱敏。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 | 详细记录 |
|---|---|---|---|---|---|---|---|---|
| E-001 | 2026-08-23 | 当前前端是否支持推理强度 | 代码 | `561d6e4`；`frontend/src/pages/chat/components/ConversationModelSelector.tsx`；`frontend/src/stores/conversation.ts` | 选择器和 store 只读写 `modelId`；新会话 run 也只在 `forwardedProps.model` 传模型。没有 reasoning preset 状态或接口。 | confirmed | active | `../../../frontend/src/pages/chat/components/ConversationModelSelector.tsx`；`../../../frontend/src/stores/conversation.ts`；`../../../frontend/src/services/stream.ts` |
| E-002 | 2026-08-23 | 当前模型目录能否表达有效推理档位 | 代码 | `561d6e4`；`llm_providers/src/llm_providers/catalog.py` | 目录只带未被业务消费的 `reasoning_replay: bool`，没有 control kind、preset、默认值、mandatory 或 replay scope；DeepSeek provider 默认显式关闭 thinking。 | confirmed | active | `../../../llm_providers/src/llm_providers/catalog.py`；`../../../llm_providers/tests/test_provider_catalog.py` |
| E-003 | 2026-08-23 | 当前工具循环是否保留 provider reasoning | 代码 | `561d6e4`；`llm_providers/client.py`；`agent/conversation.py`；`agent/sessions/session.py` | client 只把 reasoning delta 转为无正文的 `LLMActivity` 并统计长度；`LLMTurnDone` 不携带 continuation；session 投影只重建文本与 tool calls，因此续轮无法回放 reasoning。 | confirmed | active | `../../../llm_providers/src/llm_providers/client.py`；`../../../llm_providers/src/llm_providers/stream_events.py`；`../../../agent/src/agent/conversation.py`；`../../../agent/src/agent/sessions/session.py` |
| E-004 | 2026-08-23 | DeepSeek V4 的控制与 replay 契约 | 外部资料 | DeepSeek 官方文档，2026-08-23 读取 | V4 Flash/Pro 支持 thinking enabled/disabled；effort 的实际档位是 low/high/max，medium 映射 high、xhigh 映射 high；请求带 tools 时，历史 assistant reasoning 必须在后续请求完整回传，否则返回 400。 | confirmed | active | [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode) |
| E-005 | 2026-08-23 | Kimi 当前模型的控制差异 | 外部资料 | Kimi 官方文档，2026-08-23 读取 | K2.5/K2.6 支持 thinking 开关但不支持 effort；K2.7 Code/Highspeed 固定开启并强制 Preserved Thinking；K2.6 可用 `keep=all`，工具循环需要保留 reasoning；K3 才支持 low/high/max effort，但当前目录尚未包含 K3。 | confirmed | active | [Kimi Thinking Models](https://platform.kimi.ai/docs/guide/use-thinking-models)；[Model Parameter Reference](https://platform.kimi.ai/docs/api/models-overview) |
| E-006 | 2026-08-23 | OpenRouter 能否提供统一参数与逐模型元数据 | 外部资料 / 只读在线观测 | OpenRouter 官方文档与 `GET /api/v1/models`，2026-08-23 | OpenRouter 使用统一 `reasoning` 对象，并在模型元数据中给出 supported efforts、default、mandatory 和 token-budget 支持。当前 Gemini 3.7 Flash 返回 low/medium/high 且 mandatory；GPT-5.6 Luna 返回 none 到 max 的离散档位。 | confirmed | active | [OpenRouter Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)；[Models API](https://openrouter.ai/api/v1/models) |
| E-007 | 2026-08-23 | LiteLLM 是否能直接替代产品能力目录 | 外部资料 / 依赖锚点 | 锁定 `litellm==1.83.14`；Context7 对 `/berriai/litellm/v1.83.3-stable` 的官方源码/文档检索 | LiteLLM 支持 `reasoning_effort` 映射、`supports_reasoning()` 与支持参数查询，但不同 provider 的 thinking 结构和 replay 仍不同；`drop_params` 会静默丢参数，不适合作为产品有效值校验策略。 | strongly-supported | active | `../../../uv.lock`；[LiteLLM repository](https://github.com/BerriAI/litellm) |
| E-008 | 2026-08-23 | 现有 UI 是否能实现 hover 级联和选择后不关闭 | 代码 / 外部资料 | `radix-ui ^1.5.0`；现有 `DropdownMenuSub*` 封装；Radix 官方文档 | 项目已封装 Sub、SubTrigger、SubContent 和 RadioGroup/RadioItem；SubTrigger 承担指针与键盘级联，Root/Sub 支持受控 open state；item 的 `onSelect` 调用 `preventDefault()` 可阻止菜单在选择后关闭。无需新增 UI 依赖。 | confirmed | active | `../../../frontend/src/components/ui/dropdown-menu/index.tsx`；[Radix Dropdown Menu](https://www.radix-ui.com/primitives/docs/components/dropdown-menu) |
| E-009 | 2026-08-23 | reasoning 写入 session 后是否会进入诊断包 | 代码 | `561d6e4`；`frontend/src-tauri/src/diagnostics/mod.rs` | 诊断包会收集 session JSON 并递归执行凭据/路径脱敏；普通私密正文被明确保留。若新增 opaque reasoning 字段而不增加专门脱敏，它会进入诊断包。 | confirmed | active | `../../../frontend/src-tauri/src/diagnostics/mod.rs`；`../../../.cursor/rules/diagnostic-bundle-contract.mdc` |
| E-010 | 2026-08-23 | 模型与推理强度是否应视为独立运行设置 | 外部资料 | OpenAI Codex 官方配置参考，2026-08-23 | Codex 分别建模 `model` 与 `model_reasoning_effort`，且可为新任务共同设置默认值；这支持在产品交互中分别选择、在 run 配置中共同冻结。 | strongly-supported | active | [Codex Configuration Reference](https://developers.openai.com/codex/config-reference/) |
| E-011 | 2026-08-23 | 本期交互、偏好与 replay 边界 | 用户决策 | 当前需求会话，2026-08-23 | 用户确认单入口级联：一级项 hover / 键盘焦点在右侧展开；点击一级项或具体选项都不关闭整组菜单；选择模型后推理强度区域联动切换；本期不做按模型偏好但保留扩展口；provider-required replay 纳入本期。 | confirmed | active | [requirement.md](./requirement.md) |
| E-012 | 2026-08-23 | OpenAI 如何处理不可见 reasoning 的多轮续传 | 外部资料 / 官方源码 | OpenAI Responses API 与 `openai/codex`，2026-08-23 | Responses API 可返回 `reasoning.encrypted_content` 供 stateless / ZDR 多轮原样回传；Codex 协议把 reasoning 建模为独立 response item，并显式请求 encrypted content，使其随 thread model-visible history 持久化，而不是塞进 assistant 正文。 | confirmed | active | [Responses create](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)；[Codex ResponseItem](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs)；[Codex Responses transport](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/responses_websocket.rs) |
| E-013 | 2026-08-23 | Anthropic 如何处理 thinking 与工具续轮 | 外部资料 | Claude Platform 官方文档，2026-08-23 | 工具续轮必须完整、未修改地回传上一 assistant 的 thinking blocks；signature 与 `redacted_thinking.data` 都是不可解释协议字段。工具 turn 内必须保持同一 thinking mode，跨普通 turn 可由模型策略过滤旧 block。 | confirmed | active | [Claude extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) |
| E-014 | 2026-08-23 | Codex 如何组织模型能力与 thread 运行设置 API | 外部资料 / 官方源码 | `openai/codex` app-server，2026-08-23 | `model/list` 返回按目录顺序排列的 `supportedReasoningEfforts`，客户端不得自行从名称推导；`thread/settings/update` 只更新下一 turn 的 thread settings，并在变化后通知完整 effective settings；resume 默认恢复已持久化的 model 与 reasoningEffort。 | confirmed | active | [Codex app-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) |
| E-015 | 2026-08-23 | 项目是否已有敏感本地数据保护能力 | 代码 | `561d6e4`；`runtime_config/sources.py`；IM CredentialStore；session JSONL | 项目已有 OS keyring 与 AES-GCM 凭据存储，但普通会话仍是可读 JSONL；把 provider plaintext reasoning 单独升级为完整加密存储会引入新的密钥生命周期与迁移边界。本需求宜先按 provider 私有事件最小持久化并强制隔离/脱敏，不顺带重做 session-at-rest 加密。 | strongly-supported | active | `../../../runtime_config/src/runtime_config/sources.py`；`../../../agent_bridge/src/agent_bridge/protocols/im/credentials.py`；`../../../agent/src/agent/sessions/` |
| E-016 | 2026-08-23 | DeepSeek 与 Kimi 的真实工具续轮是否成功回放 continuation | 真实 LLM smoke / 运行时观测 | `561d6e4` + 当前实现 overlay；DeepSeek run `run-deepseek-core-1787478538530`；Kimi run `run-kimi-1787478433532`；本机 Tauri/bridge | 两组均用确定性 `calculate(17*23)` 触发工具循环。DeepSeek V4 Flash `high` 与 Kimi K2.6 `on` 都完成 2 次模型调用、1 次工具调用，最终返回预期结果，`model_continuations=1`、无 `RUN_ERROR`。公开 session 投影仅显示脱敏 continuation：DeepSeek 1 条 `reasoning_content`/conversation，Kimi 2 条 `reasoning_content`/tool_turn。单次 smoke 证明当前环境与输入链路可用，不构成跨时段稳定性统计。 | confirmed | active | 当前索引摘要；本机 `agent_bridge.log` 中对应 run id；`../../../agent/src/agent/conversation.py` |
| E-017 | 2026-08-23 | DeepSeek V4 Flash Vision 开启 effort 后为何无法进入 replay | 失败真实 LLM smoke / 日志 / LiteLLM 文档 | Vision run `run-deepseek-1787478433532`；`litellm==1.83.14`；Context7 `/berriai/litellm/v1.83.3-stable` | 首次调用在 83ms 内以 `LLMBadRequestError` 结束，tool/model continuation 均为 0。trace 明确显示 LiteLLM 将 Vision 识别为 OpenAI-compatible model，并在本地 `get_optional_params` 阶段拒绝 `reasoning_effort`；provider 未收到请求。LiteLLM 支持通过窄范围 `allowed_openai_params=["reasoning_effort"]` 扩展该路由白名单；`drop_params` 会静默删除参数，不符合本需求。该失败已由 E-018 的修复复验覆盖，但保留为根因 provenance。 | confirmed | retained | 本机 `agent_bridge.log` 对应 run id；`../../../llm_providers/src/llm_providers/catalog.py`；`../../../llm_providers/src/llm_providers/client.py`；[LiteLLM repository](https://github.com/BerriAI/litellm) |
| E-018 | 2026-08-23 | 窄范围放行后 Vision effort 与真实 replay 是否成功 | mock HTTP / 真实 LLM smoke / 运行时观测 | `561d6e4` + 当前实现 overlay；fresh bridge PID `39544`；Vision run `run-vision-replay-verify-1787479086040` | 兼容层只在精确 Vision route 且存在 `reasoning_effort` 时增加 LiteLLM 白名单；golden mock HTTP 证明 provider body 保留 `thinking.type=enabled` 与 `reasoning_effort=high`。真实 Vision `high` smoke 完成 2 次模型调用、1 次 `calculate`、`model_continuations=1`，最终返回预期结果且无 `RUN_ERROR`；公开 session 投影只返回 1 条 `reasoning_content`/conversation 的脱敏字符数。 | confirmed | active | `../../../llm_providers/src/llm_providers/deepseek_vision_compat.py`；`../../../llm_providers/tests/test_deepseek_vision_compat.py`；本机 `agent_bridge.log` 对应 run id |
| E-019 | 2026-08-23 | QQ 是否纳入推理强度交互范围 | 用户决策 / 代码现状 | 当前需求会话；`agent_bridge/protocols/im/control.py` | 用户确认 QQ 每轮回复尾随菜单一并增加推理强度。现状只尾随“菜单”按钮，打开后仅显示/切换模型；模型切换隐式采用目标默认 preset，QQ 无法查看或单独调整。增量范围确定为主菜单摘要、能力驱动 preset 菜单和共享 session config mutation。 | confirmed | active | [requirement.md](./requirement.md)；`../../../agent_bridge/src/agent_bridge/protocols/im/control.py`；`../../../agent_bridge/src/agent_bridge/session_models.py` |
| E-020 | 2026-08-23 | QQ 推理强度菜单是否按共享能力与并发边界实现 | 代码 / 自动化 / runtime identity | 当前实现 overlay；`test_im_control.py` 12 tests；相邻 IM 定向 110 passed；backend 2141 passed / 4 skipped；fresh bridge PID `9552` | 主菜单从 session effective config 与 public catalog 显示 label；可调 preset 分页并调用 `SessionModelService.update()`，切模型回显目标默认 preset，fixed 只读。action token 记录生成时 session/model，陈旧按钮和 busy mutation 均拒绝，不会误改新绑定或当前模型。重启后 QQ WebSocket 已恢复；未主动发真实 QQ 测试消息。 | confirmed | active | `../../../agent_bridge/src/agent_bridge/protocols/im/control.py`；`../../../agent_bridge/tests/test_im_control.py`；`../../../agent_bridge/src/agent_bridge/session_models.py`；本机 `agent_bridge.log` |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 阻塞情况 |
|---|---|---|---|
| 各 provider continuation 的最小 replay scope | 仅当前用户 turn / 含工具的 assistant 全历史 / 所有 assistant 历史 | 按官方契约建立 provider fixture，并验证投影消息序列 | 方案必须显式区分，不能用单一布尔值 |
| 诊断包如何表达 reasoning 脱敏 | 沿用 session entry 并增加专门占位 / 改为只保留 continuation 元数据 | 对当前 producer contract、consumer handler 和实际 session collector 做契约测试 | 实现前必须收敛 |
| 历史会话缺省 preset 的稳定语义 | 每次按最新目录默认解析 / 首次打开后固化当前默认 | 结合升级兼容、可解释性与未来偏好边界评审 | 设计阶段决定 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
|---|---|---|---|
| 使用能力驱动的离散 preset，不维护前端模型判断表 | E-002, E-004, E-005, E-006, E-007 | 当前 | provider 形成真正稳定且完全等价的统一契约 |
| 使用现有 DropdownMenu 级联组件，不新增 UI 依赖 | E-008, E-011 | 当前 | 真实 Tauri 验证发现子菜单定位或焦点无法满足 |
| 模型与推理 preset 原子提交并在 run 内共同冻结 | E-001, E-003, E-010, E-011 | 当前 | 会话执行模型改为完全无状态且不再有工具续轮 |
| provider reasoning 仅作为 provider 私有 continuation 保存和回放，不进入普通消息或诊断包 | E-003, E-004, E-005, E-006, E-009, E-012, E-013, E-015 | 当前 | provider 全部取消 reasoning replay 要求，或项目统一升级 session-at-rest 加密协议 |
| session runtime-config API 返回完整 effective config，目录负责有序能力集合 | E-010, E-011, E-014 | 当前 | 项目改为 provider 原生 UI 且不再维护统一会话状态 |
| 本期不实现跨会话按模型偏好 | E-011 | 当前 | 用户另开范围变更并回到需求阶段确认 |
| OpenAI-compatible Vision 只显式放行目录已确认的参数，不全局丢弃或放行未知参数 | E-007, E-017, E-018 | 当前，已验证 | provider/transport 改为原生识别该模型并提供等价参数契约 |
| QQ 复用统一目录与 `SessionModelService.update()`，不拥有独立 reasoning 状态或模型判断表 | E-011, E-019, E-020 | 当前，已验证 | QQ 改为只读 surface 或用户另行定义 IM 独立偏好 |

## Artifact 映射

| 证据 ID | 原始位置 | 归档位置 | 完整性 | 敏感性与访问说明 |
|---|---|---|---|---|
| E-001~E-003, E-008~E-009 | Git working tree at `561d6e4` | 当前仓库路径 | Git SHA + 路径锚定 | 代码可公开；不含用户数据 |
| E-004~E-006, E-010, E-012~E-014 | 官方公开文档 / 官方源码 / 公开只读模型 API | 本索引保留 URL、代码路径与摘要 | 未做网页快照 hash；以读取日期标识时点，Codex 源码以 main 当前内容为准 | 公开 |
| E-007 | 锁文件与官方 LiteLLM 文档/源码 | 当前仓库 `uv.lock` + URL | 锁版本已锚定；Context7 文档版本为相邻 stable | 公开 |
| E-011 | 当前用户决策 | [requirement.md](./requirement.md) | 已写入变更记录 | 不含私密正文 |
| E-015 | 当前代码 | 当前仓库路径 | Git SHA + 路径锚定 | 只记录存储机制，不含 secret 或用户数据 |
| E-016~E-018 | 本机真实 LLM smoke session、SSE 与 `agent_bridge.log` | 本索引保存脱敏汇总；不归档原始 reasoning 或完整 prompt/output | 以 model、run id、事件计数和 runtime identity 锚定；测试 session 在汇总后清理 | 日志和原始 session 仅限本机；索引不含 secret 或 reasoning 正文 |
| E-019 | 当前用户决策与 IM control 代码 | 需求、方案与本索引 | 用户确认 + 路径锚定 | 不含用户 QQ 内容、openid 或凭据 |
| E-020 | 当前 IM control 代码与自动化结果 | 当前仓库路径与本索引摘要 | overlay 路径 + 测试数量/命令锚定 | fake provider/session；不访问真实 QQ，不含用户数据 |

## 对账记录

| 日期 | 对账范围 | 结果与修订 |
|---|---|---|
| 2026-08-23 | requirement、当前代码、provider 官方文档、Radix 与 Codex 官方资料 | 一致；确认 UI-only 改动不足以安全交付，设计必须覆盖 session config、run freeze、provider mapping、opaque replay 与诊断脱敏。 |
| 2026-08-23 | 用户交互补充、OpenAI Responses / Codex、Claude thinking、项目敏感存储能力 | 更新：确认 hover 展开和选择后不关闭可由现有 Radix 能力实现；continuation 应作为 provider 私有项原样回放；session 配置 API 应返回完整 effective state。 |
| 2026-08-23 | 当前实现 overlay、源码新鲜 Tauri/bridge、DeepSeek/Kimi 真实工具循环、Vision 失败 trace 与 LiteLLM 当前文档 | 更新：DeepSeek V4 Flash 与 Kimi K2.6 replay 已由真实 provider 调用确认；Vision 在本地 LiteLLM 参数门禁失败，不能沿用前述整体完成判断，AC-11/AC-12 重新打开。 |
| 2026-08-23 | Vision 精确 route 修复、golden mock HTTP、backend 全量门禁与 fresh bridge 真实工具 replay | 更新：窄范围白名单保留 provider `reasoning_effort`，Vision 完成 2-call replay；E-017 保留为历史根因，E-018 成为当前通过证据，AC-11/AC-12 关闭。 |
| 2026-08-23 | 用户 QQ 范围确认、IM control 实现、定向与 backend 全量回归 | 更新：QQ 共享 session/catalog config，preset 菜单与 stale/busy guard 已验证；E-019 保留范围决策，E-020 记录当前实现证据，AC-17 关闭。 |
