# 110 · 动态上下文缓存连续性 · 证据索引

## 范围与口径

本索引记录需求 110 的基线、当前代码锚点、同类官方实现和关键局限。它只保存内容无关结论与公开来源，不归档 prompt、response、Memory、工具正文、Skill 指令、session 标识或逐 item digest。

## 证据表

| ID | 日期 | 问题 | 来源类型 | 版本/锚点 | 结论 | 局限 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E-001 | 2026-08-29 | 真实长会话的跨用户回合最早断点和缓存表现是什么 | 需求 109 真实 LLM smoke | merge commit `af033a5`；109 E-025/E-026 | cold-ish 第二请求 `32768/37093=88.34%`，warm 三轮第三请求 `36736/37180=98.81%`；两者均为 `cross_user_turn/rebase`，最早断点 `runtime`，messages 正常追加、未重写 | provider cold/warm 状态不同；lost-prefix token 是 item 比例估算，不是 runtime item 真实大小 | active |
| E-002 | 2026-08-29 | runtime 为什么跨用户回合 rebase | 当前代码 | `af033a5`；`agent/src/agent/conversation.py`、`agent/src/agent/tool_runtime/context.py` | 每次 inference 前刷新精确到秒的 runtime，并改变 run/sequence/supersedes；runtime 仅参与当前组装，不进入跨用户回合可延续 transcript | 代码阅读说明机制，不独立量化 provider token 影响 | active |
| E-003 | 2026-08-29 | 当前工具时间是否为 action-time | 当前代码 | `af033a5`；`datetime_calculate`、`todo_manage`、`scheduled_task_manage` | `datetime_calculate` 没有明确 current/now operation；多个工具使用推理前快照，部分路径会直接读取 wall clock，尚无统一宿主时钟边界 | 需要设计和确定性时钟测试确认所有调用路径 | active |
| E-004 | 2026-08-29 | Codex 的 session history 与 prompt cache 契约是什么 | OpenAI Codex 官方源码/测试 | `openai/codex@6478a751fde8884b2fdc76486fe23175a8e795d4`；`state/session.rs`、`session/turn.rs`、`stream_events_utils.rs`、`prompt_caching.rs` | `SessionState.history: ContextManager` 是模型历史权威；每次 sampling 从 `clone_history().for_prompt()` 生成 input；tool call 与无工具 assistant 都写回同一 history。连续请求测试要求无变化时只追加新 user，变化时按 settings/permissions/environment/user 顺序追加 | Codex 使用 Responses `ResponseItem` 和长期 Session；agent-friend 需要适配现有 `Message`、事件投影与每 dispatch 重建 Conversation 的生命周期 | active |
| E-005 | 2026-08-29 | Codex 如何表达粗粒度日期和 timezone | OpenAI Codex 官方源码 | 同一固定 commit；`context/world_state/environment.rs` | environment state 包含 current date/timezone；后续通过 state diff 只表达发生变化的字段 | Codex 的完整 environment state 还含 filesystem/network 等字段，不能整体照搬 | active |
| E-006 | 2026-08-29 | Codex 如何取得最新当前时间 | OpenAI Codex 官方源码/测试 | 同一固定 commit；`current_time.rs`、`tools/handlers/current_time.rs`、`current_time_reminder.rs` tests | 宿主 `TimeProvider` 提供当前时间；`clock.curr_time` 在工具调用时读取最新值。测试明确允许先前 reminder 时间早于随后工具返回时间 | Codex 还支持精确 current-time reminder；需求 110 只采用粗日期 + action-time 工具，不采用该 reminder | active |
| E-007 | 2026-08-29 | OpenAI Prompt Caching 的核心命中边界是什么 | OpenAI 官方文档 | `developers.openai.com/api/docs/guides/prompt-caching`，访问于 2026-08-29 | 缓存依赖请求开头的精确前缀；稳定内容应放前面，变化内容放后面；cache key 不能替代内容前缀 | provider 行为为 best effort，文档可能更新；实现时需重新核验 | active |
| E-008 | 2026-08-29 | 显式 breakpoint 和 mid-conversation system 是否可作为 provider 增强 | Anthropic 官方文档 | Prompt Caching、mid-conversation system messages、deferred tools，访问于 2026-08-29 | Anthropic 支持显式缓存边界和中途 system 内容，可用于稳定前缀与按需工具的 provider 层优化 | DeepSeek 等 provider 没有等价通用能力，不能成为核心正确性依赖 | active |
| E-009 | 2026-08-29 | DeepSeek Context Caching 依赖什么 | DeepSeek 官方文档 | `api-docs.deepseek.com/guides/kv_cache/`，访问于 2026-08-29 | DeepSeek 自动识别从开头开始的公共前缀，没有通用显式 breakpoint；append-only 连续性是主要本地保证 | provider TTL、路由和命中仍由外部系统控制 | active |
| E-010 | 2026-08-29 | 时间、Memory 与 session history 的产品语义如何取舍 | 用户需求/design review | 当前需求会话确认 | cache epoch 首次提供粗日期/timezone，之后只在跨日/timezone 变化时追加；具体当前时间由专门工具读取。Memory 不做引用、去重或复用机制。model conversation history 是权威，不新增完整 prepared wire ledger/LRU；final-wire witness 只验证 | 需要真实长会话总输入数据评估 append-only 增长；agent-friend 的进程内 session runtime 生命周期仍需实现验证 | active |
| E-011 | 2026-08-29 | Codex 对动态上下文使用什么角色和生命周期 | OpenAI Codex 官方源码/测试 | 同一固定 commit；`session/world_state.rs`、`session/time_reminder.rs`、`prompt_caching.rs` | world-state/time reminder 等变化先作为 conversation item 写入 Session history；settings/permissions 更新使用 developer，environment/date 使用可信 user context，TurnContext 仍只是一轮快照 | agent-friend 没有公共 developer role；设计需要用现有 system/user 层级表达等价信任关系 | active |
| E-012 | 2026-08-30 | 实现是否按确认结构保持连续性、action-time、epoch 与隐私边界 | 代码实现 / 确定性验证 | branch `feature/110-dynamic-context-cache-continuity`；基线 `af033a5` + 当前 overlay；三阶段项目门禁 | `SessionManager` 仅持最小 session runtime state，不保存 prepared wire；普通回合和工具续轮 strict extension，粗 calendar 仅跨日/timezone 追加，current/Todo/Scheduled Task 使用 action-time；Memory 当前回合出现/相同/变化/消失可内容无关诊断；provider transform 单独 rebase；JSONL/compaction 不新增 model-only 正文。backend `2352 passed, 4 skipped`，frontend `670 passed` + build，Rust test + release check | 外部 provider 结果见 E-013；用户已验收 | active |
| E-013 | 2026-08-30 | 优化后的真实长会话 prefix、cache read、总输入和动态时间行为如何 | 用户授权的真实 LLM smoke / 产品 Conversation 路径 | `af033a5` + overlay `a4f2d144...`；`deepseek/deepseek-v4-flash`；安全 ref `24f9616a08f5`；83,633 chars、80 messages、13 complete turns、50 tool events、0 attachments；普通 3 次 + 动态 4 次调用 | 普通三轮分别 `1280/37258=3.44%`、`37248/37359=99.70%`、`37376/37444=99.82%`；后两次由 109 的 runtime rebase 变为 `cross_user_turn/strict_extension`，`runtime_changed=false`、lost prefix `0`。相对 109 cold-ish 第二次 `32768/37093=88.34%`，新第二次总输入增加 `266` tokens（约 `0.72%`），cache read 增加 `4480`，普通输入从 `4325` 降为 `111`；相对 109 warm 第三次 `36736/37180=98.81%`，新第三次总输入增加 `264`（约 `0.71%`），普通输入从 `444` 降为 `68`。动态场景 current initial `4608/38519`，工具续轮 `38528/38635`，跨日 `38656/38752`，timezone `38656/38866`；全部 baseline/strict extension，跨日和 timezone 的 `first_divergence_domain=runtime` 表示尾部 contributor delta，不是 rebase，lost prefix 均为 `0`。current 只调用一次并返回匹配 action-time；calendar item `1→2→3`；精确 runtime marker `0` | 首请求分别只有 3.44%/11.96% 读取，继续证明 cold-ish/provider 预热影响显著；单次受控长会话不能外推所有自然流量。append-only 使普通请求总输入约增加 0.7%，后续仍需观察 Memory/Skill 增长与 compaction | active |

## 未决问题

| 问题 | 当前无法区分的解释 | 最有区分力的下一项证据 | 是否阻塞 |
| --- | --- | --- | --- |
| runtime rebase 的真实 token 机会量级 | E-013 已在同一长会话中消除 rebase，并观察到第二请求普通输入减少 `4214`、总输入增加约 `0.72%`；但 cold/warm、provider 路由与单样本仍共同影响结果 | 更新后自然流量报告按 comparison scope/domain 聚合；不要把本次单样本差额外推为全局收益 | 不阻塞需求，影响收益外推 |
| append-only Memory/Skill delta 是否明显增加总输入 | 自然请求中 contributor 变化可能很少，也可能在长会话中累计 | report 同时比较 cache read、总输入、contributor delta 数量和 compaction 频率 | 不阻塞 M1，阻塞后续增长优化 |
| provider breakpoint 是否有额外收益 | 严格公共前缀可能已足够；Anthropic/OpenAI 显式边界可能进一步改善写入/TTL | provider-independent M1 完成后做同 wire 的受控对照 | 不阻塞核心设计 |
| 动态工具是否值得单独交付 | Skill/MCP schema 变化频率和 token 占比尚无自然样本 | M1 contributor/tool prefix observation + 单独 deferred tool 需求分析 | 不阻塞，已移出本期实现 |

## 决策影响

| 决定 | 依赖证据 | 当前状态 | 重新打开条件 |
| --- | --- | --- | --- |
| 以 session 内统一 append-only model history 解决跨用户回合 runtime rebase，不保存完整 prepared wire | E-001、E-002、E-004、E-009～E-011 | confirmed | deterministic final wire 无法保持业务/工具语义，或真实 smoke 证明总输入显著恶化且无合理 compaction 边界 |
| 采用粗日期/timezone + action-time 工具，不采用周期性精确时间 reminder | E-003、E-005、E-006、E-010 | confirmed | 真实 bad case 证明模型在有稳定政策和时间工具时仍系统性无法判断何时取时，并且更小的补救无法解决 |
| Memory 原样按回合追加，不做引用/去重 | E-010 | confirmed | 总输入证据证明 Memory 增长成为主要成本，且独立需求确认新的语义与隐私方案 |
| deferred tools 移出本期，仅保留 contributor/delta 边界 | E-008、E-009、E-010 | confirmed | M1 设计无法在不实现动态工具的情况下稳定核心 tools 前缀 |
| 应用重启无法精确恢复时建立新 epoch | E-004、E-009 与隐私原则 | confirmed | 存在不持久化敏感或可逆内容、又能证明精确 wire 身份和顺序的恢复协议 |

## Artifact 映射

| 证据 | 原始位置 | 本需求归档 | 完整性与敏感性 |
| --- | --- | --- | --- |
| E-001 | `docs/requirements/109-prompt-cache-observability-and-optimization/` | 引用原始 requirement/evidence/progress，不复制 smoke stdout | Git commit + 脱敏汇总；不含 prompt/response/session id |
| E-002～E-003 | 当前仓库 `af033a5` 源码 | 原位保留 | Git commit 可复查；不归档运行时正文 |
| E-004～E-006、E-011 | `openai/codex` 固定 commit | 仅保存固定 URL/commit 与摘要 | 公开源码；不复制第三方正文 |
| E-007～E-009 | provider 官方文档 | 仅保存 URL、访问日期和摘要 | 动态网页，后续关键设计需重新核验 |
| E-010 | 当前需求 review | requirement 与本索引 | 只保存用户确认的语义结论，不保存无关会话内容 |
| E-012 | 当前 feature overlay、自动化输出和 `scripts/prompt-cache-smoke` | 源码/测试原位；本索引只保留计数与结论 | 不归档 prompt、response、Memory、工具结果、原 session id 或逐 item digest；真实 smoke 尚未执行 |
| E-013 | 本次真实 smoke 的临时 stdout 与临时 session store | 仅在本索引/progress 保留脱敏聚合，stdout 不归档，临时 store 自动删除 | 绑定 commit、overlay、模型、安全 ref、规模与调用量；不保存 prompt、response、原 session id、request/response id、Memory、工具正文或逐 item digest |

## 对账记录

| 日期 | 对账范围 | 结果 |
| --- | --- | --- |
| 2026-08-29 | 需求 109 baseline、当前 Conversation/runtime/time/tool 路径、Codex prompt caching/world state/current time、OpenAI/Anthropic/DeepSeek 官方资料、用户 requirement review | 一致：跨回合 rebase 根因是临时 runtime 重装配；M1 采用 session model history、粗日期/timezone delta 和 action-time 工具；Memory 保持简单追加；deferred tools 后续独立交付。尚未创建 design 或进入实现。 |
| 2026-08-29 | 用户 design 修订；Codex `SessionState.history`、TurnContext、world state、output recording 与 prompt caching tests | 一致：model history 是唯一 transcript 权威；RunContinuationProjection 不升级为完整 wire store，SessionManager 只保留最小进程内 runtime state；witness 仅验证。动态环境使用可信 user item，权限/政策使用更高优先级 item。 |
| 2026-08-30 | 当前实现、确定性 AC、三阶段项目门禁与扩展后的 long-session smoke harness | 一致：无完整 wire ledger/LRU；model-only context、assistant/tool transaction 进入统一进程内 history；当前时间只在工具 action-time 读取；Memory 诊断改为相对当前用户输入的 recall。真实 provider 行为仍等待本次授权。 |
| 2026-08-30 | 用户授权的 7 次 DeepSeek long-session smoke、需求 109 E-025/E-026 对照 | 一致：普通跨用户回合已从 runtime rebase 变为 strict extension；工具续轮、跨日和 timezone delta 同样只追加尾部。cache read 明显提高但首请求仍 cold-ish，且总输入约增加 0.7%，因此结论同时保留 prefix 收益与 append-only 增长成本。 |
