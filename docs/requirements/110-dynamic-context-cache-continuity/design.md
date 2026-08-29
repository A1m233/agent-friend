# 110 · 动态上下文缓存连续性 · 技术方案

## 状态

CONFIRMED

## 1. 方案摘要

本方案不新增一套以“上一份完整 prepared provider request”为权威的通用 ledger。模型上下文的唯一运行时权威是一份 session 级、进程内的 `SessionModelHistory`：动态宿主事实、真实用户输入、provider assistant output、tool transaction、Skill 和 Memory 都按模型可见顺序进入同一份 append-only history；下一次请求从该 history 正常生成。

`TurnContext`/`ToolRunContext` 继续只承载当前用户轮的执行资源与可信宿主能力，不再承载每次 inference 都重新渲染的完整 model-visible runtime snapshot。final-wire witness 只验证 adapter/provider 最终请求是否保持严格前缀，不能参与 transcript 构造，也不能成为历史真相源。

时间采用已经确认的激进策略：cache epoch 首次提供粗粒度本地日期与 timezone；之后只在跨日或 timezone 改变时追加 delta；不注入精确时间 reminder。当前时刻和所有时间敏感动作在工具实际执行时读取统一宿主 `TimeProvider`。

需求行为和验收边界见 [requirement.md](./requirement.md)，证据锚点见 [evidence-index.md](./evidence-index.md)。

## 2. 设计依据

### 2.1 Codex 对照

固定参考 `openai/codex@6478a751fde8884b2fdc76486fe23175a8e795d4`：

- `TurnContext` 明确是单 turn 快照。
- `SessionState.history: ContextManager` 是长生命周期模型历史。
- 每次 sampling 从 `sess.clone_history().for_prompt(...)` 生成 input。
- world state、权限、时间 reminder、user、assistant、tool call 与 tool output 都作为 conversation item 进入相同 history。
- 无变化的下一用户轮只追加新 user；发生变化时把 settings/permissions/environment delta 放在新 user 之前。
- settings/permissions 使用 developer 层级，environment/date 使用可信 user context fragment。

agent-friend 不照搬 Codex 的 Rust 类型、Responses `ResponseItem` 或持久化 rollout；只采用“Session model history 是权威、turn context 是快照、变化进入 history、wire witness 只验证”的边界。

### 2.2 当前实现缺口

当前 `Conversation` 具有以下相互关联的问题：

1. `AgentRuntime` 每次 dispatch 都重新 `open(session)` 并 `start_conversation(session)`；`Conversation` 和 `ContextManager` 实例只活一轮，注释中的 session 级状态在真实入口上并不成立。
2. `RunContinuationProjection` 只活一个 run，并保存上一份 prepared messages；run 结束时 `close()` 清空，所以不能承接下一用户回合。
3. `_ephemeral_model_context_message()` 每次 inference 都刷新精确时间并改变 run/sequence/supersedes，且只进入当前组装。
4. tool call assistant 会在工具循环中记录到 projection，但无工具最终 assistant 在返回前没有进入可跨 run 使用的 provider transcript 状态。
5. surface、Skill filesystem scope、Memory 和 capability catalog 仍分别通过临时 turn context 或 system prompt 重建。
6. final-wire witness 已经能诊断 strict extension/rebase，但没有一份长期 model history 可让本地装配自然满足严格扩展。

## 3. 核心决策

### D-1 · `SessionModelHistory` 是唯一模型历史权威

新增最小的 `SessionModelHistory`，存储 agent 层规范化的 `Message` item，而不是完整 `ModelRequest`、provider request body 或 adapter final wire。

它包含：

- 从业务 Session 投影出的 user/assistant/tool 历史；
- 只活在当前进程的 model-only context item；
- provider 返回的完整 assistant turn 投影，包括原始 tool arguments 和 provider reasoning state；
- 当前 history 与业务 `active_events` 的对齐锚点。

稳定 system prompt、tools、request settings、model/provider route 仍属于请求 envelope，不复制进 `SessionModelHistory`。请求生成规则是：

```text
stable system envelope
+ SessionModelHistory.for_prompt()
+ current staged tail
```

其中 staged tail 一旦成为实际 model request 的组成部分，就按顺序提交到 `SessionModelHistory`；后续 request 从提交后的 history 继续。

### D-2 · 不建立通用 ledger store

`SessionManager` 内部增加一个私有 `dict[session_id, SessionRuntimeState]`，仅用于弥合“每次 dispatch 重建 Conversation”与“model history 必须跨 dispatch 存活”的生命周期差异。

这不是公共 registry，也不提供查询、持久化、LRU、跨 manager 共享或完整 wire 缓存能力。生命周期如下：

- 同一个 `SessionManager` 多次 `start_conversation`：复用同一 runtime state。
- session 删除：同步移除 runtime state。
- transient manager：每个请求拥有独立 state。
- 新 `SessionManager` 或应用重启：没有旧 state，明确开始新 cache epoch。

`SessionRuntimeState` 只持有：

```python
@dataclass
class SessionRuntimeState:
    model_history: SessionModelHistory
    context_manager: ContextManager
    model_context_state: ModelContextState | None
    prompt_cache_trace_tracker: PromptCacheTraceTracker
    cache_epoch: CacheEpochState
```

`ContextManager` 从当前的 per-Conversation 构造上移到该 runtime state，真正成为 session 级策略状态；摘要 circuit breaker、usage calibration 和 prompt lineage 不再随 dispatch 重置。

### D-3 · `RunContinuationProjection` 不再保存完整请求

不把 `RunContinuationProjection` 的 `previous_messages` 生命周期直接延长为跨 session wire cache。实施时提取并复用其中两类正确逻辑：

- `ProviderAssistantTurn` 对 text/tool call/reasoning state 的保真投影；
- tool transaction 完整性校验。

这些产物直接追加到 `SessionModelHistory`。`RunContinuationProjection` 随后删除，或缩减为不拥有历史的单次 response accumulator；不能继续作为第二份 transcript 权威。

### D-4 · Witness 只验证，不驱动历史

`ProviderRequestWitness` 和需求 109 trace tracker 保留内容无关 previous request 状态，用于：

- 比较 strict extension/rebase/repeat；
- 计算 common prefix 与 first divergence；
- 发现 renderer、adapter 或 provider transform 是否破坏本地 append-only history；
- 建立观测用 cache epoch。

Witness 结果不能插入、删除、替换或重新排序 `SessionModelHistory`。provider transform 导致 rebase 时，history 保持不变，只更新诊断状态和 cache epoch。

### D-5 · Cache epoch 与 model history rewrite 分离

下列变化结束旧 cache epoch，但不自动清空 conversation history：

- persona/system prompt；
- core tools/schema；
- request settings；
- model/provider route；
- provider transform 使 final wire 不再严格扩展。

只有真实历史治理行为才替换 model history：

- compaction/摘要折叠；
- FIFO 截断或 tool context rewrite；
- edit/resend、rollback 或失败清理使业务 active history 改写；
- 应用重启后从可持久化业务 Session 重建运行时 history。

这些 rewrite 同时建立新 cache epoch，并由 observation 明确分类。

## 4. 运行时结构

### 4.1 `SessionModelHistory`

建议落位：`agent/src/agent/sessions/model_history.py`。

最小职责：

```python
@dataclass
class SessionModelHistory:
    items: list[Message]
    active_event_uuids: tuple[str, ...]

    def snapshot(self) -> list[Message]: ...
    def append(self, items: Sequence[Message]) -> None: ...
    def reconcile(self, session: Session) -> HistoryReconcileResult: ...
    def replace(self, items: Sequence[Message], *, reason: str) -> None: ...
```

约束：

- `snapshot()` 返回副本，不让调用方就地修改权威历史。
- `append()` 只接受已经完成排序和信任分类的 item。
- `reconcile()` 只处理 fresh `Conversation` 与业务 Session 的一致性：active event UUID 是旧锚点的严格扩展时补入外部新增业务事件；发生 rewrite 时用 `session.llm_messages` 重建并返回 epoch reason。
- `replace()` 只供明确 compaction/rewrite 使用。
- 不提供 provider request、tools/settings 或 cache key API。

### 4.2 Canonical item 格式

history 保存现有 `agent.messages.Message`：

- user/assistant/tool 继续复用已有角色和 metadata；
- provider assistant text 使用 `content or ""`；
- provider tool calls 保留 `arguments_json`；
- `ProviderReasoningState` 放入现有 `provider_reasoning_state` meta，由当前 rendering 路径恢复 provider 字段；
- model-only item 标记 `meta["model_only"] = True` 和稳定的 `prompt_cache_domain`；
- 不保存 adapter 最终 request body。

这样 ContextManager、附件 renderer、provider reasoning replay 和需求 109 item-domain 观测继续使用统一 `Message` 边界。

### 4.3 `ModelContextState`

不设计 contributor registry。只定义一个固定状态：

```python
@dataclass(frozen=True)
class ModelContextState:
    calendar: CalendarState
    surface: InteractionSurfaceId | None
    channel: PromptChannel
    approval_mode: SessionApprovalModeState
    runtime_capabilities: RuntimeCapabilities
    skill_catalog_fingerprint: str | None
```

比较函数返回固定顺序的 model-only messages：

```text
settings/channel constraints
permission delta
environment/calendar/surface delta
Skill catalog delta
```

没有通用 registration、priority、dependency graph 或动态 contributor discovery。未来 deferred tools 独立设计时可以复用“状态比较后追加 item”的语义，不要求复用当前类型。

## 5. 请求与 history 生命周期

### 5.1 新进程或首次打开 session

1. `SessionManager.start_conversation` 为 session 创建 `SessionRuntimeState`。
2. `SessionModelHistory` 从 `session.llm_messages` 构造业务历史基线。
3. 标记 cache epoch reason：新 session 为 `session_start`；已有业务历史但无 runtime state 为 `process_resume_degraded`。
4. 捕获当前有效 `ModelContextState`，生成 epoch 首次 snapshot。
5. snapshot、当前 user、Skill/Memory 按顺序进入 staged tail。
6. 从稳定 system + history + staged tail 生成请求。

恢复出的业务历史可能仍被 provider 命中，但本地 observation 不报告 exact continuation。

### 5.2 同进程下一用户回合

1. 新 `Conversation` 取得同一 runtime state。
2. `reconcile()` 验证业务 active events 未改写。
3. 捕获当前 context state，只生成变化 delta。
4. 追加顺序为：

```text
previous history
previous provider assistant
boundary context delta (if any)
current user
query-dependent Skill/Memory (if any)
```

5. envelope 未变且 ContextManager 未 rewrite 时，新 request 应严格扩展旧 final wire。

### 5.3 工具续轮

provider 返回 `LLMTurnDone.assistant_turn` 后，无论包含 text、reasoning、tool call 的哪种组合，都先转换为一个 assistant history item。工具执行结果完成后按 model call 顺序追加 tool item。

下一次 inference 前再次检查边界 context state；通常无变化，因此续轮只增加：

```text
assistant tool call
tool result(s)
```

跨日或 timezone 改变时，在 tool result 后追加 calendar delta。并行工具仍按 provider tool call 顺序写入 history，不按线程完成顺序重排。

### 5.4 无工具最终回答

当前缺口必须修正：只要 provider 返回了完成的 `assistant_turn`，即使没有 tool calls，也在 turn 完成前追加到 `SessionModelHistory`。业务 assistant event 和 model history item 使用同一 provider result 派生，避免下一用户回合只能从持久化简化投影重建。

### 5.5 失败与重试

- transport 在任何可用 response item 前失败：不产生 assistant item；下一次恢复按现有 failure sanitation 判断是否重建 history/epoch。
- provider 对同一 request 的透明 transport retry：不重复追加 staged input。
- response recovery 主动追加 host advisory：该 advisory 一旦进入恢复请求即作为 model-only history item；恢复改变 request 尾部但不回写旧 item。
- partial assistant：继续服从现有 Session sanitation；不可安全重放时建立 rewrite epoch，不把半截 output 当作完成 assistant。
- provider witness 不可用：history 仍正常追加，但 comparison precision 降级。

## 6. 动态 context item

### 6.1 角色与顺序

依据 Codex 的语义分层，而不是统一改成 system：

| Context | agent-friend role | 位置 | 原因 |
| --- | --- | --- | --- |
| calendar、timezone、surface、环境能力事实 | synthetic `user` | 当前真实 user 之前 | 是宿主提供的当前环境事实，不是高优先级行为政策；延续现有可信 marker/用户 marker 转义边界 |
| permission、channel 行为约束、runtime policy delta | model-only `system` | 当前真实 user 之前 | 属于宿主约束，不能被普通用户内容覆盖；等价于 Codex developer 层语义 |
| Skill catalog | 现有 model-only system 语义 | 当前真实 user 之前或 epoch 初始 | 保留 fingerprint gating |
| Skill activation/filesystem scope | 现有 Skill 指令层级 | 当前真实 user 之后 | 与当前请求相关；scope 明确 `current_user_turn` |
| Memory recall | `system` | 当前真实 user 之后 | 只服务当前 query；进入 history 后保持原位置 |

真实用户输入继续经过 internal marker escaping，不能伪造 synthetic host context。

### 6.2 Delta 内容

移除每 inference 改变的 run、sequence 和精确 timestamp。状态消息只包含真实变化字段；示意：

```xml
<agent_friend_environment_context source="host">
  <calendar date="2026-08-29" timezone="Asia/Shanghai" />
  <surface type="desktop_chat" />
</agent_friend_environment_context>
```

变化时可以使用 contributor-local version/supersedes，但 version 只在该 contributor 实际变化时递增，不能使用每 request 全局序号。具体 XML 字段保持小而固定，由 snapshot tests 锁定。

### 6.3 Channel 与 capability catalog

- `ChannelSection` 从基础 system prompt 移出。
- voice/text 行为指令成为 channel model context；channel 不变时不重复追加，切换时追加 delta。
- tool capability catalog 不再在 `_assemble_stream` 中拼接到 system prompt；core tools 稳定时作为 epoch 初始 model-only item进入 history。
- core tools/schema 变化仍是 cache epoch；本期不实现 deferred tool discovery。

### 6.4 Skill 与 Memory

- Skill catalog 沿用现有 fingerprint，不变不追加。
- Skill 激活指令和 filesystem scope 在一个用户轮内只追加一次；工具续轮不重复。
- scope 通过 `lifetime="current_user_turn"` 自然失效，不需要删除旧 item。
- 每个非空 Memory recall 在对应 user 后原样追加；无召回不追加。
- 不做 Memory 引用、相同内容去重、digest 替换或 selection 复用。
- 新的 runtime history 不增加动态正文持久化；既有业务/Skill event 契约不作为恢复 exact continuation 的证明。

## 7. 当前时间模块

### 7.1 `TimeProvider`

建议新增 `agent/src/agent/time_provider.py`：

```python
class TimeProvider(Protocol):
    def now(self) -> datetime: ...

class SystemTimeProvider:
    def now(self) -> datetime:
        return datetime.now().astimezone()
```

约束：

- 返回 timezone-aware datetime；naive datetime fail closed。
- bridge composition root 只构造一个 `SystemTimeProvider`。
- 同一实例注入 `SessionManager`/Conversation、TodoService、ScheduledTaskService 和需要“现在”的工具。
- 测试使用可变 fake provider，不 patch 全局 `datetime`。

### 7.2 Calendar snapshot/delta

在 user input 或 tool output 后、下一次 inference 前读取一次 `TimeProvider.now()`：

- runtime state 没有 calendar：追加当前 date/timezone snapshot。
- date 与 timezone 均相同：不追加。
- date 改变：追加 calendar delta。
- timezone 改变：追加 timezone 及该 timezone 下当前 date 的自洽 delta。
- 跨分钟不触发任何 context item。

新 compaction window 重建有效 context snapshot；不实现 interval/boundary current-time reminder。

### 7.3 `datetime_calculate operation=current`

tool schema 新增唯一分支：

```json
{
  "operation": "current"
}
```

返回至少包含：

```json
{
  "operation": "current",
  "current_datetime": "2026-08-29T12:34:56+08:00",
  "date": "2026-08-29",
  "timezone": "Asia/Shanghai",
  "utc_offset": "+08:00"
}
```

`$now`、`$today` 和 `calendar_relation` 使用同一次工具 invocation 读取的 action-time。operation 命名使用 `current`，避免和参数 sentinel `$now` 混淆。

### 7.4 Action-time 注入

`ToolBatchExecutor` 已经在线程 worker 真正开始 raw invocation 时调用 `invocation_args(...)`。因此 `_tool_args_with_invocation_context` 在这里读取 `TimeProvider.now()`，而不是 inference 前刷新 `ToolRunContext.trusted_now`。

统一改造当前所有隐藏 now 消费者：

- `datetime_calculate`；
- `todo_manage`；
- `scheduled_task_manage`；
- `conversation_history` 中相对当前时间的展示路径。

Todo/ScheduledTask service 的创建时间、未来校验和相对 schedule 解析也使用同一 provider。即使模型先调用 `current`、数秒后再创建提醒，mutating tool 仍重新读取 action-time。

`ToolRunContext` 继续持有 URL evidence、filesystem authorization 和 command handles；时间职责改为持有/访问 `TimeProvider`，`render_for_model()` 不再渲染精确时钟。

### 7.5 稳定时间政策

更新稳定 runtime/tool usage policy，明确：

- calendar context 只代表当前有效本地日期和 timezone；
- 询问具体当前时刻必须调用 `datetime_calculate(current)`；
- 旧 current tool result 是 `as-of` 历史结果，不代表以后仍是现在；
- 创建提醒、计划和相对延迟由执行工具自行读取 action-time，不能复用模型看到的旧值。

## 8. ContextManager、compaction 与增长

### 8.1 正常路径

ContextManager 使用 `SessionModelHistory.snapshot()` 作为模型历史输入；当前 boundary delta、user 和 query context 作为 staged tail 参与预算计算。未触发折叠/截断时，BuildResult 必须等于稳定 system + 原 history + staged tail，不能重新投影旧动态 item。

### 8.2 Rewrite 路径

出现以下任一结果即视为明确 model history rewrite：

- `new_compaction`；
- `dropped_count > 0`；
- tool context projection 删除或替换旧 tool result；
- prior checkpoint 与 runtime history 无法对齐。

接受该请求后，用实际 BuildResult 中除稳定 system 外的规范化 messages 替换 `SessionModelHistory`，记录 rewrite reason 并建立新 cache epoch。

### 8.3 动态正文与 compaction

新的 model-only dynamic item 标记为 runtime-only，不作为新增持久化正文。compaction 开始新 epoch时：

- 业务 user/assistant/tool 历史继续由既有 Session/compaction contract 提供；
- 旧 calendar/surface/permission delta 不需要进入新摘要，改为重建当前完整 snapshot；
- 非持久化 Memory recall 不作为应用重启后的恢复来源；
- 不为保存 exact continuation 写入额外 prompt、Memory、工具正文或可逆 digest。

实现时必须增加隐私测试，证明 compaction event、Session JSONL、diagnostics 和 trace 没有因 `SessionModelHistory` 引入新的动态正文落盘。

## 9. Provider 与 prompt-cache 观测

### 9.1 Provider-independent 核心

- DeepSeek：依赖最终 token 从第 `0` token 开始的公共前缀；本期核心只保证真实 append-only history。
- OpenAI/Anthropic：本期不新增显式 breakpoint 接口；现有/未来 adapter 可以在不改变 history 的前提下使用 provider capability。
- cache key 继续使用 session namespace，但不参与 history identity 判断。
- tools schema 保持稳定；动态 tool discovery 后续独立交付。

### 9.2 Observation 对接

`PromptCacheTraceTracker` 生命周期移到 `SessionRuntimeState`，使不同 `Conversation` 实例仍共享 previous request observation。final-wire witness 继续由 provider adapter 生成。

新增/调整诊断字段：

- `model_history_relation`: `append` / `rewrite` / `process_rebuilt`；
- `cache_epoch_reason`；
- `session_runtime_continuation`: `exact` / `degraded`；
- contributor delta domains；
- provider witness relation。

本地 history 为 append 但 provider witness 为 rebase 时，明确归因 `provider_transform_changed`；不能把它改写成 history rewrite。

## 10. 并发与一致性

- 现有 session turn guard 继续保证一个 session 同时最多一个 mutating turn。
- `SessionRuntimeState` 只在持有 session turn guard 的路径修改；只读 diagnostics 返回快照。
- 并行 read tools 可以并发执行，但 tool results 按原 provider tool call 顺序追加。
- persona/model/permission mutation 继续先取得现有 session mutation guard，再更新 cache epoch/context state。
- 如果有入口绕过 `SessionManager.start_conversation` 或 session guard，确定性测试必须暴露；不为兼容该入口复制 runtime state。

## 11. 主要代码影响

| 路径 | 设计改动 |
| --- | --- |
| `agent/src/agent/sessions/model_history.py` | 新增最小 `SessionModelHistory` 与 reconcile/append/replace 结果类型 |
| `agent/src/agent/sessions/manager.py` | 私有 session runtime state 映射；复用 session 级 ContextManager/history/trace；删除 session 时清理 |
| `agent/src/agent/conversation.py` | 从统一 history 组装；追加 input/output/tool/context item；移除 ephemeral time snapshot 与 full-request projection 权威 |
| `agent/src/agent/run_transcript.py` | 移除或缩减 `RunContinuationProjection`，保留必要的 provider assistant/tool transaction helper |
| `agent/src/agent/context/*` | 接受 session model history；rewrite 时明确 replace/epoch；不在正常路径重建旧动态 item |
| `agent/src/agent/system_prompt/*` | 移出 ChannelSection 动态正文；稳定时间/当前工具政策；保留可信 marker 约束 |
| `agent/src/agent/tool_runtime/context.py` | 移除 inference-time 精确时间渲染；接入 TimeProvider/action-time |
| `agent/src/agent/time_provider.py` | 新增宿主时钟协议与系统实现 |
| `agent/src/agent/tools/builtin/datetime_calculate/tool.py` | `current` operation；所有相对 now 在 invocation-time 解析 |
| Todo/ScheduledTask/ConversationHistory 相关 service/tool | 统一 action-time 来源 |
| `agent_bridge/src/agent_bridge/assembly.py` | 构造并注入同一个 SystemTimeProvider |
| `agent/src/agent/prompt_cache_trace.py` | session runtime 生命周期、history relation 与 witness-only 诊断 |
| `llm_providers/src/llm_providers/prompt_cache_observation.py` | 如公共 schema 需要，增加 epoch/history relation 的可空向后兼容字段 |

不新增数据库表、Session event 正文类型、跨进程 transcript 文件或 provider-specific cache 依赖。

## 12. 验证设计

### 12.1 Session model history

- 同一 `SessionManager` 连续创建两个 `Conversation`，断言复用同一 runtime history，但对象本身不同。
- 无变化普通回合：第二 final wire 严格扩展第一 final wire。
- 无工具最终 assistant 在下一 Conversation 的 history 中存在且只出现一次。
- 单/并行 tool call：assistant request 与全部 tool result 顺序、call id 和 raw arguments 保真。
- 新 `SessionManager` 打开同一持久化 Session：history 从业务事件重建，continuation 标为 degraded/new epoch。
- edit/resend/rollback/compaction：明确 replace history 和 epoch，不伪装 append。

### 12.2 Contributor

- calendar/surface/permission/channel/runtime capability/Skill 分别改变，只追加对应 item。
- 未变化时不追加 model context。
- voice/text 切换不改变稳定 system prompt。
- Memory 不变、变化、首次出现、随后无召回均保持按回合位置；不做 dedupe。
- user 伪造内部 marker 被转义，不能取得 trusted context 语义。

### 12.3 时间

- fake TimeProvider 跨分钟：无 delta。
- 跨日：下一边界一条 calendar delta。
- timezone 变化：一条自洽 date/timezone delta。
- `current` 返回 invocation-time。
- `$now/$today/calendar_relation` 使用 action-time。
- todo/scheduled task 在 current 查询后继续推进 fake clock，断言 mutating action 使用更新后的时间。
- 工具续轮不重复 calendar/context。

### 12.4 Witness 与隐私

- history append + witness strict extension。
- history append + 人工 provider transform rebase：history 不变，诊断归因 provider transform。
- process restart 不报告 exact continuation。
- Session JSONL、compaction、logs、diagnostics、observations 不新增 runtime/Memory/tool正文或可逆 digest。

### 12.5 真实 LLM smoke

确定性门禁通过后才进入需求 109 对照 smoke。每次运行前按 `llm-api-confirm` 说明模型、route、长会话场景、cold/warm 设计和预计调用量并取得明确授权。

报告至少包括：cache read/input total、普通输入、总输入、comparison scope、strict extension/rebase、first divergence、contributor delta、cold/warm 状态及 compaction/epoch 边界。

## 13. 实施顺序

1. 建立 `SessionRuntimeState`/`SessionModelHistory`，把 ContextManager/trace 生命周期提升到 session。
2. 将普通 user、provider assistant、tool transaction 接入统一 history，替换 run-local full-request projection。
3. 拆分固定 model context state/delta，移除 ephemeral runtime message、动态 ChannelSection 和 system 头部 capability catalog 重写。
4. 引入 TimeProvider、calendar delta 和 `datetime_calculate(current)`，完成 action-time 工具链。
5. 对接 compaction/rewrite/epoch 和隐私门禁。
6. 完成确定性 AC、项目完整检查，再按授权执行真实长会话 smoke。

每一步都必须保持业务 Session 事件投影、工具协议和 provider reasoning replay 可用；不得先删除现有 projection 再依赖后续步骤补回正确性。

## 14. 明确不做

- 不保存上一份完整 prepared provider messages 作为权威状态。
- 不新增通用 ModelContextLedger、ledger store、LRU 或 contributor 插件框架。
- 不持久化新的 model-only runtime history 正文。
- 不实现精确时间 reminder。
- 不实现 deferred tools/tool search/MCP 动态 schema。
- 不让 final-wire witness 参与 transcript 构造。
- 不以 cache hit ratio 替代总输入、正确性、权限或 action-time 验证。
