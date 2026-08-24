# 102 · Provider transcript 前缀复用 - 证据索引

## 用途与范围

- 档案归属：[requirement.md](./requirement.md)
- 本索引记录：DeepSeek dashboard 观测、请求指纹诊断、受控重复请求、真实工具续轮 A/B、用户确认的需求边界，以及 Phase 2 需要补齐的外部实践证据。
- 本索引不记录：API key、完整 prompt、用户会话正文、工具敏感参数、原始 reasoning 内容或完整模型输出。
- 当前代码基线：`6a657d1` 加 `feature/102-provider-transcript-cache-reuse` 工作区实现 overlay；overlay 尚未提交。

## 当前综合判断

DeepSeek 的低总体缓存命中不是“长消息或大量工具天然无法缓存”。完全相同请求的四组真实重复调用均接近全量命中；实际 Agent 工具续轮则在新 assistant 响应和重新生成的临时 system context 处破坏 provider 可见前缀。相同输入下的真实 tool-loop A/B 已把当前重建方式的 `0%` 与 provider transcript 延续方式的 `99.73%` 区分开，足以确认主要机制和修复方向。

DeepSeek 官方 Harness 的当前实现进一步关闭了三个方案疑点：动态时间与 runtime policy 以带 provenance 的 `UserMessage` 追加；assistant 使用统一冻结 content blocks 加 adapter-private `replayState`；请求只允许由 append-only session log 与完整 request-header snapshot 重建，surface replacement 以单调 `replaceGeneration` 断代。agent-friend 首期应适配为 run-local、非持久化的追加 surface，以继续满足 068 的“不进入长期历史/摘要/记忆”边界。

本轮对照 Codex、Kimi、Anthropic 及 agent-friend 实际 LiteLLM `1.83.14` 请求链后，方案 review 点已经全部关闭：canonical history 与 compaction 继续由 ContextManager 拥有；活动 run 只保存上一份 prepared request 和 completed provider response 的临时投影；partial/unclosed response 不进入 continuation；动态 runtime snapshot 在完整 tool-result batch 后以 host `user` packet 追加；最终 strict-extension 必须由 provider transform 之后的 per-call transport witness 证明。

同时确认当前调查 overlay 的 `_request_payload_bytes()` 发生在 LiteLLM `transform_request` 之前，只能作为 adapter-input 级 fingerprint，不能继续命名为 final-wire identity。设计已要求用同步/异步 transport request hook 读取 fully prepared outgoing body，并以真实 body 的有序 transcript items 和 request envelope 生成 content-free witness。

当前证据只能证明单次 run 内工具续轮有显著可回收空间。它不能证明跨独立用户回合复用是安全的，也不能把受控 case 的 `99.73%` 外推为 DeepSeek dashboard 的整体命中率。

实现后的 4-call 真实验收进一步确认：completed provider assistant + tool result + superseding host snapshot 的第二请求在最终 wire 上是 `strict_extension`，缓存命中 `43,008 / 43,197 = 99.5625%`，达到 AC-7。与此同时，本轮 `rebase` 对照也被 DeepSeek 按 184-item 公共前缀命中 `45,312 / 45,509 = 99.567%`。因此 strict continuation 是 agent 侧“旧请求是否完整保留”的正确性证据，不是 provider 命中的必要条件或同义词；服务端仍可能缓存较短公共前缀，也可能因路由、TTL 或其它因素不命中。

## 证据账本

| ID | 日期 | 问题或主张 | 类型 | 关键锚点 | 摘要 | 置信度 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-22 | DeepSeek 官网样本的实际输入缓存比例 | 用户截图 / provider dashboard | `deepseek-v4-flash-vision-exp`；cached `430,080`；uncached `2,192,882` | 输入 token 命中率为 `430,080 / 2,622,962 ≈ 16.4%`；截图同时显示输出 `6,446`。这是聚合观测，不包含逐请求因果。 | confirmed | active |
| E-002 | 2026-08-23 | 消息长度或工具数量是否天然导致低命中 | 真实 LLM 受控实验 | 2×2 场景，每组 exact repeat 两次 | 2 messages/no tools `99.69%`；2 messages/26 tools `99.88%`；184 messages/no tools `99.86%`；184 messages/26 tools `99.93%`。排除“长历史/工具多必然低命中”的普遍解释。 | confirmed | active |
| E-003 | 2026-08-23 | 真实连续请求首先在哪里分歧 | 本地日志 / 请求指纹 | 66 DeepSeek requests；54 continuous transitions | 54/54 transition 的完整前缀均未保留；53/54 首次分歧位于新 assistant；54/54 的 tools 保持稳定；66 次请求的 system fingerprint 全部不同。 | confirmed | active |
| E-004 | 2026-08-23 | 当前代码为何移动 provider 前缀 | 代码 | `agent/src/agent/conversation.py`；`agent/src/agent/context/protocol.py` | 每次 inference 刷新 transient model context；请求重新按 system/extra/history/current turn/tail 装配，工具续轮插入真实 assistant/tool result 后又生成新的临时尾部。 | confirmed | active |
| E-005 | 2026-08-23 | 保留真实 provider transcript 是否能恢复命中 | 真实 LLM A/B | 184 stable messages；26 stable tools；同一真实 tool call/result | 当前 tail rebuild probe：prompt `42,107`、cached `0`；provider transcript continuation probe：prompt `42,097`、cached `41,984`，命中约 `99.73%`。 | confirmed | active |
| E-006 | 2026-08-23 | 早期 `append_only_ledger` 实验能否证明真实 ledger | 实验复核 | smoke 场景已改名 `synthetic_message_extension` | 该场景只追加合成 JSON，没有回放 provider 实际 response，因此不能作为 provider transcript ledger 证据；保留用于防止误读实验。 | confirmed | retained |
| E-007 | 2026-08-24 | 首期范围和验收口径 | 用户决策 | 当前需求会话 | 首期限定单次 Agent run；`>=95%` 仅是受控 DeepSeek case 的验收条件；结构变化时正确性优先；Phase 2 形成 design 前先调研别人的公开实践。 | confirmed | active |
| E-008 | 2026-08-24 | 官方 provider 如何定义可复用工具续轮 | 官方协议文档 | DeepSeek cache/tool guides；OpenAI Responses/prompt caching；Anthropic thinking/tool caching | 共同原则是保留旧前缀、回放 provider-owned response/reasoning/tool-call state、再追加 tool result；工具、thinking、图片、压缩和配置变化可能构成失效边界。 | confirmed | active |
| E-009 | 2026-08-24 | 成熟 Agent 如何维护 transcript 与动态状态 | 官方源码 | Codex `0a80fab`；Kimi Code `9a71582`；Kimi CLI `cbc15c0` | Codex 追加 typed response items/world-state diff，并以 `history_version` 标记改写；Kimi 使用 append-only history、tool exchange 邻接约束、动态 reminder/tool diff 和显式 compaction replacement。 | confirmed | retained |
| E-010 | 2026-08-24 | Kimi 较高命中是否能证明 provider 天生更好 | 对照推断 | Kimi 官方客户端源码 + 本需求 DeepSeek exact-repeat/A-B | Kimi 客户端形态本身更利于缓存，足以解释观测差异的一部分；仍无受控证据证明 Kimi 服务端缓存天然优于 DeepSeek。 | supported inference | active |
| E-011 | 2026-08-24 | DeepSeek 是否承诺 tool result 后追加 system runtime update | 官方 API schema 缺口 | Chat Completions `messages` 支持 system/user/assistant/tool，但未明确中途 system 的顺序语义 | 动态 trusted time/scope 的最终 wire 形态必须通过受控 DeepSeek capability smoke 验证；未通过时 correctness-first 重建。 | unresolved | active |
| E-012 | 2026-08-24 | DeepSeek 官方 Agent 如何追加逐步时间与动态 policy | 官方源码 | `deepseek-harness` `b150a551`；`time-context`、`runtime-context.ts`、`fs-sandbox` | 时间在 pre-step 作为带来源 `UserMessage` 追加；runtime policy 变化追加 superseding snapshot；新增内容位于既有前缀之后。该实践绕开了中途 system 位置契约。 | confirmed | active |
| E-013 | 2026-08-24 | Provider response 应保存完整 SDK response、统一消息还是由 UI 事件重建 | 官方源码 | `deepseek-harness` `message.ts`、`agent.ts`、`replay.ts`；Codex typed ResponseItem | 成熟实现保存不可变的 provider-neutral assistant blocks，并只附带 adapter-private、provider/model 绑定的最小 replay metadata；stream 结束时一次组装完成消息，不从用户可见文本反推。 | confirmed | active |
| E-014 | 2026-08-24 | Transcript generation 与 compaction/header 变化由谁管理 | 官方源码/测试 | `deepseek-harness` session surface + request reconstruction tests | append-only log 是事实源；纯追加保持上一请求前缀；surface replace 增加 `replaceGeneration`；system/tools/model/reasoning 等 request envelope 变化写完整 header snapshot；测试证明请求可从 log 字节级重建。 | confirmed | active |
| E-015 | 2026-08-24 | DeepSeek Harness 的持久动态时间能否原样照搬 agent-friend | 需求对账 | 068 R-2.7/AC-12/AC-13 与 Harness time-context | 不能原样持久化。068 明确禁止动态端与时钟进入 session history/summary/memory；102 只授权单 run，因此采用 run-local append surface，run 结束或恢复重建时丢弃/重建。 | confirmed | active |
| E-016 | 2026-08-24 | Canonical history、run-local continuation 与 final request comparison 应如何分工 | 官方源码，code-supported | Codex `e3609f2`：`context_manager/history.rs`、`client.rs`、`protocol/models.rs`、`responses_retry.rs` | ContextManager 拥有 ordered history 与 rewrite version；turn-scoped ModelClientSession 只保存上一完整请求和 completed output items，并仅在非 input 字段相同、input 严格延长时使用 continuation；stream retry 使用冻结请求，只有 completed output item 进入后续状态。该边界直接支持“ContextManager 语义 owner + run-local transport projection”。 | confirmed | active |
| E-017 | 2026-08-24 | Completed response、partial stream 与 tool adjacency 的安全边界 | 官方源码/文档，code-supported | Kimi CLI `cbc15c0`；Anthropic SDK TS `bfa9197`；Anthropic streaming/tool-use/extended-thinking docs | Kimi 仅在 step 完整结束后把 assistant message 与全部 tool results 增长进 Context，compaction 显式 clear/rebuild；Anthropic final-message API 在提前结束时失败，tool-use/thinking block 不支持 partial recovery，且 assistant tool-use 后必须立即跟对应 tool results。支持 completed-only continuation、partial rebase 和 host snapshot 只能位于完整 result batch 之后。 | confirmed | active |
| E-018 | 2026-08-24 | LiteLLM 后怎样取得并发隔离的最终 outgoing request | 官方源码/库文档 + 本地版本核对，code-supported | LiteLLM `1.83.14` / tag `3d2b8fe`；HTTPX `b5addb6`；本地 `.venv` 安装版本 `1.83.14` | LiteLLM 在 SDK 输入之后执行 provider `transform_request`、合并 `extra_body` 并把 per-call client 传给底层 handler；HTTPX request hook 在 request fully prepared、尚未发送时运行，AsyncClient 使用 async hook。按 transport 注入独立 client 可以观察真实 body 且不使用全局 callback。不同 provider 的 client 类型不同，因此必须由 transport-specific adapter 和真实 HTTP body contract test 承担。 | confirmed | active |
| E-019 | 2026-08-24 | 当前 overlay 的 request fingerprint 是否已经是 final wire | 本地代码对账，code-supported | `llm_providers/src/llm_providers/client.py::_request_payload_bytes`；LiteLLM `transform_request` 调用顺序 | 否。当前 helper 在 `prepare_litellm_request` 后、LiteLLM provider transform 前自行序列化部分字段；无法覆盖 provider transform、stream 字段、SDK 序列化及不同 client route。它可保留为 adapter-input 辅助，但必须由 E-018 的 transport witness 取代 final-wire 声明。 | confirmed | active |
| E-020 | 2026-08-24 | 实现是否在真实 HTTP 边界保持严格前缀且不泄漏正文 | 本地确定性实现验证 | `test_request_witness.py`、`test_run_transcript.py`、DeepSeek Vision/Kimi MockTransport contract tests、完整 `scripts/check` | actual HTTP body 的第二请求被判定为 `strict_extension`；同请求 503 retry 的两个 body 完全一致并报告 `same_request_retry`；completed assistant 保留 null content、原始 tool arguments 和 reasoning replay；runtime sequence/supersedes 与 tool adjacency 有集成断言；witness 只保留 digest/尺寸。完整门禁为 backend `2174 passed, 4 skipped`、frontend `608 passed`、Rust `222 passed, 1 ignored`。 | confirmed | active |
| E-021 | 2026-08-24 | 实现后的真实 DeepSeek 工具续轮是否达到 AC-7 | 经授权真实 LLM smoke | `deepseek-v4-flash-vision-exp`；4 calls；184 stable messages；26 tools；completed provider turn + host snapshot | strict continuation warm：prompt `43,045`、cached `0`、relation `baseline`；probe：prompt `43,197`、cached `43,008`、uncached `189`、relation `strict_extension`、common prefix `185`，命中 `99.5625%`。rebase 对照 probe 也命中 `45,312 / 45,509 = 99.567%`、relation `rebase`、common prefix `184`，证明 wire relation 与实际缓存命中必须分栏解释。 | confirmed | active |

## 已知局限与待补证据

| 问题 | 当前证据边界 | Phase 2 最有区分力的证据 | 阻塞情况 |
| --- | --- | --- | --- |
| 动态 runtime context 如何在保留前缀时刷新 | DeepSeek Harness 与 Kimi 都使用追加的 user-role host context；Codex 使用追加的 developer context；无需依赖中途 system | [design.md](./design.md) 已定义 run-local trusted runtime snapshot、supersede 语义、内部 marker 转义和同推理工具时间绑定 | 设计已关闭，待实现验证 |
| 哪些变化必须重建 | 已形成 correctness-first 矩阵：未接受重试可复用；partial/unclosed、compaction/rollback/edit/recovery、模型/工具/附件/provider transform 变化默认重建 | [design.md](./design.md) 已把矩阵映射到 ContextManager、provider witness、recovery 与测试 | 设计已关闭，待实现验证 |
| Provider transcript 的公共边界 | DeepSeek、OpenAI、Anthropic 官方契约和 Codex/Kimi 官方源码已对账 | [design.md](./design.md) 已定义 provider-neutral run projection + provider-owned completed turn/replay state | 设计已关闭，待实现验证 |
| 068 非持久化边界与 102 run 内旧快照可见性 | 068 禁止进入长期模型历史；102 要求同 run 旧 prefix 不原地改写且新状态 supersede | [design.md](./design.md) 已定义 run-local projection、host snapshot sequence/supersedes 与 finally 销毁 | 设计已关闭，待实现验证 |
| 最终 wire witness 如何跨 LiteLLM/Kimi/DeepSeek Vision route 取得 | 已证明不存在一个可安全假设适用于所有 provider 的统一高层 callback；per-call transport hook 能观察 fully prepared request | 各 route 已用 mock HTTP transport、同步/异步取消回归和 client close 断言覆盖；真实 DeepSeek smoke 继续观察 relation | 确定性实现已关闭，AC-7 待真实验证 |
| 对 dashboard 整体比例的实际提升 | 当前样本中 run continuation 约占输入 token 的一部分，首次请求仍不可由 run-local 机制消除 | 实现后按 call index、run 和 rebase reason 聚合一段真实使用数据 | 不阻塞首期设计，不得预先承诺整体比例 |

## Artifact 映射

| 证据 ID | 原始位置 | 当前归档 | 完整性与访问说明 |
| --- | --- | --- | --- |
| E-001 | 用户提供的 DeepSeek dashboard 截图 | 当前会话附件与本索引脱敏数字摘要 | 截图不含 API key；不复制到仓库 |
| E-002～E-006 | 当前 feature 分支的 smoke、测试、源码与本机实验输出 | 调查 overlay；外部调查报告 `C:\Users\example-user\.codex\visualizations\2026\08\23\01a02f2f-a338-7dd0-b713-a596d44ea4f6\deepseek-cache-investigation.md` | 索引保留聚合值；原始 prompt、response 和 reasoning 不入库 |
| E-007 | 当前用户决策 | [requirement.md](./requirement.md) 与本索引 | 已写入需求边界和变更记录 |
| E-008～E-011 | 官方文档与固定 SHA 官方源码 | 外部调查报告 `C:\Users\example-user\.codex\visualizations\2026\08\23\01a02f2f-a338-7dd0-b713-a596d44ea4f6\deepseek-cache-investigation.md` 与本索引 | 只保存公开链接、版本锚点和结论，不复制大段第三方源码 |
| E-012～E-015 | DeepSeek Harness 固定 SHA 源码、Codex/Kimi 固定 SHA 源码与本仓 068 文档 | 同一外部调查报告、本索引及公开 commit 链接 | DeepSeek Harness 临时 clone 仅供核对；索引保留 SHA/路径/结论，临时目录消失后仍可由公开链接复查 |
| E-016 | Codex 官方仓库 `e3609f2d02a5896c391fa4c07335165c9272b686` | [history.rs](https://github.com/openai/codex/blob/e3609f2d02a5896c391fa4c07335165c9272b686/codex-rs/core/src/context_manager/history.rs)、[client.rs](https://github.com/openai/codex/blob/e3609f2d02a5896c391fa4c07335165c9272b686/codex-rs/core/src/client.rs)、[models.rs](https://github.com/openai/codex/blob/e3609f2d02a5896c391fa4c07335165c9272b686/codex-rs/protocol/src/models.rs)、[responses_retry.rs](https://github.com/openai/codex/blob/e3609f2d02a5896c391fa4c07335165c9272b686/codex-rs/core/src/responses_retry.rs) | 公开固定 SHA；本轮从 `068c49f` 更新到 remote HEAD 后确认相关文件无差异 |
| E-017 | Kimi CLI `cbc15c076d17f70fec9f89c90c0502e68657f505`；Anthropic SDK TS `bfa9197f0182084941052be9752c948638421601`；官方文档 | [Kimi context](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/src/kimi_cli/soul/context.py)、[dynamic injection](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/src/kimi_cli/soul/dynamic_injection.py)、[KimiSoul](https://github.com/MoonshotAI/kimi-cli/blob/cbc15c076d17f70fec9f89c90c0502e68657f505/src/kimi_cli/soul/kimisoul.py)、[Anthropic stream source](https://github.com/anthropics/anthropic-sdk-typescript/blob/bfa9197f0182084941052be9752c948638421601/src/lib/BetaMessageStream.ts)、[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)、[tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) | 公开固定 SHA 与官方文档；不归档第三方正文 |
| E-018 | LiteLLM `v1.83.14-stable` `3d2b8fed3281f60fcf6908c43df7823d6966897d`；HTTPX `b5addb64f0161ff6bfe94c124ef76f6a1fba5254` | [LiteLLM transform/handler](https://github.com/BerriAI/litellm/blob/3d2b8fed3281f60fcf6908c43df7823d6966897d/litellm/llms/custom_httpx/llm_http_handler.py)、[LiteLLM HTTP clients](https://github.com/BerriAI/litellm/blob/3d2b8fed3281f60fcf6908c43df7823d6966897d/litellm/llms/custom_httpx/http_handler.py)、[HTTPX event hooks](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/docs/advanced/event-hooks.md) | 公开固定 SHA；本地安装源码仅用于确认仓库实际版本和调用顺序，不作为唯一长期 artifact |
| E-019 | 当前 feature 分支未提交调查 overlay | [design.md](./design.md) 现状分析与 `git diff` 可复查 | 历史 provenance 是 `6a657d1 + overlay`；后续实现会替换或降级该 helper，不能把未来 commit 反写成此次调查基线 |
| E-020 | 当前 feature 分支未提交实现 overlay | `agent/tests/test_run_transcript.py`、`llm_providers/tests/test_request_witness.py`、`scripts/check/test_prompt_cache_smoke.py` 与本轮完整门禁输出 | 测试 fixture 不含生产 API key；MockTransport 捕获正文只在测试进程内断言，不写日志或文档 |
| E-021 | 本轮经授权执行的 `scripts/prompt-cache-smoke` 标准输出 | 本索引与 [progress.md](./progress.md) 的聚合计数 | 不归档完整 prompt、tool arguments、reasoning 或 API key；记录模型、调用量、消息/工具规模、usage、wire relation 和聚合比例即可复核 AC-7 |

## 决策影响

| 当前决定 | 依赖证据 | 重新打开条件 |
| --- | --- | --- |
| 先修单次 run 内工具续轮，不做跨独立用户回合 | E-003、E-005、E-007 | 实现后证据表明未命中主要来自跨回合首次请求，且动态上下文跨轮语义已有安全方案 |
| 受控 DeepSeek case 使用 `>=95%`，不设 dashboard 整体硬目标 | E-001、E-002、E-005、E-007 | provider 官方缓存规则变化，或实验包络不再能稳定复现 |
| 结构变化时正确性优先、允许重建 | E-003～E-005、E-007 | 能证明新的 transcript 表达在所有相关状态变化下仍保持等价 |
| design 采用“事实源之上的 run-local continuation projection + 显式 rebase generation”，不新增第二份 durable ledger | E-005、E-008、E-014、E-016 | 现有事件投影无法无损重建 provider response，或某 provider 必须持久化 opaque response handle |
| Kimi 观测只作为客户端实践参考，不作为 provider 优劣结论 | E-002、E-005、E-009、E-010 | 出现同请求、同模型级别、同网关条件的受控跨 provider 证据 |
| Dynamic runtime update 使用 run-local host `user` snapshot，不依赖中途 system | E-012、E-015 | 真实模型行为证明 user snapshot 无法维持可信语义，且存在更可靠的合法角色映射 |
| Provider 层在 stream 完成时产出统一 assistant turn + 最小 adapter replay state，不保存整份 SDK response | E-008、E-009、E-013 | 某 provider 的合法续轮无法由统一 blocks + 有界 replay metadata 无损表达 |
| ContextManager 决定语义上的 candidate/rebuild；run transcript 不复制预算与 compaction 策略 | E-014～E-016 | 现有 ContextManager 无法给出可验证 projection anchor 时，先补 anchor，不把策略移入 provider client |
| Provider transport 以 per-call final-wire witness 验证 strict extension；不使用 LiteLLM 全局 callback | E-018、E-019 | 目标 route 无法注入安全的独立 client/request hook，或真实 body contract test 证明 hook 位置不在最终 transform 后 |
| Partial/unclosed response 不进入 continuation；host runtime snapshot 只能在完整 tool-result batch 后追加 | E-012、E-016、E-017 | 某 provider 官方协议提供可证明安全且保持工具/reasoning 完整性的更强恢复能力 |
| DeepSeek 中途 system placement 不再阻塞首期设计；原 API 缺口作为历史局限保留 | E-011、E-012 | 后续明确选择 system-role 动态消息时重新打开 |

## 对账记录

| 日期 | 对账范围 | 结果 |
| --- | --- | --- |
| 2026-08-24 | `requirement.md`、新建 `design.md`、当前 `Conversation/ContextManager/LLMClient` overlay、Codex/Kimi/Anthropic/LiteLLM/HTTPX 锚点 | 一致。四个方案 review 点均已有证据关闭；E-019 明确纠正当前 overlay 的 final-wire 命名；仍未执行实现、确定性测试或新的真实 LLM smoke。 |
| 2026-08-24 | Phase 3 implementation、actual HTTP contract tests、provider/context 全回归、frontend/Rust 门禁与 AC-7 smoke harness | 一致。E-020 关闭确定性机制与项目门禁；唯一未完成项是经单次授权执行 4 次 DeepSeek 强制工具请求并核对第二请求缓存比例。 |
| 2026-08-24 | 经授权的 4-call DeepSeek AC-7 smoke、E-020 deterministic evidence 与实现后诊断口径 | 一致。E-021 以 `99.5625%` 关闭 AC-7，并保留 rebase 对照同样高命中的附带发现；不把受控结果外推到 dashboard 整体比例。 |
