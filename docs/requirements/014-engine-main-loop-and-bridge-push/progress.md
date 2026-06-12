# 014 · 引擎主循环与桥推送通道 — 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
IN_PROGRESS

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织，便于实施时逐项打勾、断点续传。
> 依赖顺序：M14.1 → M14.2 → M14.3 → M14.4 → M14.5 → M14.6 → M14.7 → M14.8。

### M14.1 Session schema 加性扩展
- [x] `agent/src/agent/sessions/events.py`：`EventType` Literal 加 `system_trigger` / `memory_observation`；`ALLOWED_EVENT_TYPES` frozenset 加同两项；`SCHEMA_VERSION` 不递增（docstring 加 014 段说明）
- [x] 既有 10 个 sessions 单测全绿（加性扩展不破老路径）

### M14.2 Conversation 内部新增
- [x] `agent/src/agent/conversation.py`：`__init__` 加两个可选参数 `tool_hook_invoker: ToolHookInvoker | None = None` + `post_turn_external: bool = False`
- [x] 顶部加 `ToolHookInvoker` type alias（避免 runtime/ 包形成循环依赖）
- [x] 顶部 import 加 `ConversationFragment, Utterance` + `Literal`
- [x] `_append_system_trigger_event(source_kind, system_prompt_addendum, output_visibility)` 私有助手
- [x] `_append_memory_observation_event(uuid, ts, text, source_kind, persona_id)` 私有助手
- [x] `dispatch_system_turn` 公共方法（user / memory_only 两路径完整实现，silent turn 自构 fragment 喂 memory）
- [x] `_invoke_tool_safely` 改造：`tool_hook_invoker` 非 None 时走 invoker，否则保留原行为
- [x] `stream` 的 finally 块：按 `post_turn_external` 决定是否跳过硬编码 `_observe_turn`（默认 False = 老行为）
- [x] `agent/tests/test_conversation_dispatch_system_turn.py`：7 个测试（user visibility yield + 落 assistant_message / memory_only 不 yield / 不落 assistant_message 落 memory_observation / 自构 fragment speaker=agent 1 utterance + source_ref 关联 / messages 派生不被污染 / system_trigger marker 落事件）—— 全绿
- [x] `agent/tests/test_conversation_tool_hook.py`：3 个测试（PROCEED 调 default_invoke + 旁路观察 / BLOCK 不调真 tool 但写 tool_call_result+is_error / None 时行为完全不变）—— 全绿
- [x] 全 agent 套件 133 测试全绿（原 123 + 新 10），既有路径零退化

### M14.3 AgentRuntime 核心
- [x] `agent/src/agent/runtime/__init__.py`：包导出 + `AgentRuntime` 类（含 `_register_default_hooks` / `_dispatch` / `start` / `stop` / `register_source` / `register_hook` / `tool_hook_invoker` 桥接方法 / `last_dispatch_finished_at` property）
- [x] `agent/src/agent/runtime/inbox.py`：`AgentEvent` discriminated union（UserEvent / SystemTriggerEvent）
- [x] `agent/src/agent/runtime/hooks.py`：`HookKind` / `PreTurnDecision` / `PreToolUseDecision` / `PostTurnContext` / `HookRegistry`（含 Pre-\* 短路 + 错误隔离 + 抛异常视为 PROCEED）
- [x] `agent/src/agent/runtime/listeners.py`：`Subscriber` / `PushEnvelope` / `ListenerRegistry`（fan_out_event + turn 边界打包 + thread→asyncio bridge + 满队列/loop closed 不阻塞 dispatch thread）
- [x] `agent/tests/test_runtime_inbox.py`：5 测试（字段形态、frozen 不可变、queue round-trip）
- [x] `agent/tests/test_runtime_hooks.py`：12 测试（注册顺序、PreTurn SKIP 短路、PreToolUse BLOCK 短路、四点位错误隔离、hook 据事件类型分支）—— AC-2 + AC-3
- [x] `agent/tests/test_runtime_dispatch.py`：8 测试（UserEvent dispatch + listener fan-out envelope kind/seq/source_kind/events、SystemTriggerEvent user-visibility 路径、Subscriber kind 过滤、PreTurn SKIP 真的让 dispatch 跳过、tool_hook_invoker 桥接 PreToolUse BLOCK + PostToolUse 旁路）—— AC-1 + AC-5 runtime 层
- [x] `agent/tests/test_runtime_silent_turn.py`：4 测试（silent turn listener 0 envelope、session 落 system_trigger + memory_observation 没 assistant_message、messages 派生不被污染、memory.observe 调一次—默认 PostTurn hook 正确跳过 silent turn 避免重复）—— AC-6 runtime 层
- [x] `agent/tests/test_runtime_observe_migration.py`：5 测试（post_turn_external=False 走老 finally / True 跳过老 finally；同一 session.events 两条路径产出 `ConversationFragment` 完全相等：field-by-field + `==` 兜底；带 assistant 多段文本路径；memory=None 默认 hook no-op）—— AC-4
- [x] 全 agent 套件 167 测试全绿（原 133 + M14.3 新增 34）

### M14.4 EventSource 实例
- [x] `agent/src/agent/runtime/sources.py`：`UserSource`（无 thread，submit 入队 UserEvent）/ `BedtimeSource`（A 类，独立 thread 跑 schedule loop）/ `IdleReflectionSource`（D 类 silent turn，依赖 `runtime.last_dispatch_finished_at`）
- [x] BedtimeSource / IdleReflectionSource 内 thread + `_stop_evt` Event；fire_now 立即触发；schedule loop 用 `Event.wait(timeout)` 可中断；构造期参数校验（hour/minute 范围、idle_minutes>0）
- [x] `_seconds_until_time_of_day` 纯函数：今日 / 跨夜两条路径；now_factory 注入便于测试
- [x] `agent/tests/test_runtime_sources.py`：16 测试（_seconds_until 3 case + UserSource 3 + BedtimeSource 5 含真线程 loop 触发 + IdleReflectionSource 5 含真线程超阈值 fire / 不超阈值不 fire）—— AC-5 / AC-6 source 层覆盖
- [x] 全 agent 套件 183 测试全绿（M14.3 167 + 新 16）

### M14.5 Bridge 集成
- [x] `agent_bridge/src/agent_bridge/settings.py`：加 `dev_mode` / `enable_bedtime` / `bedtime_target_session_id` / `bedtime_hour` / `bedtime_minute` / `enable_idle_reflection` / `idle_target_session_id` / `idle_minutes`（全部 backward-compat 默认值，老 .env 不需要改）
- [x] `agent_bridge/src/agent_bridge/agent_runtime_factory.py`：新文件 `build_agent_runtime(settings, session_manager, memory)`；conversation_factory 闭包：每次 dispatch 通过 SessionManager 装配 Conversation，注入 `post_turn_external=True` + `tool_hook_invoker=runtime.tool_hook_invoker`
- [x] `agent_bridge/src/agent_bridge/assembly.py`：`BridgeRuntime` 加 `agent_runtime: AgentRuntime | None = None` 字段；`build_runtime` 装配
- [x] `agent_bridge/src/agent_bridge/push/protocol.py`：`encode_envelope_sse`（复用 `agent.runtime.PushEnvelope` 作为 wire schema，避免重复定义）
- [x] `agent_bridge/src/agent_bridge/push/__init__.py`：`/push/subscribe` GET 端点（长 SSE，订阅者注册到 `ListenerRegistry`，按 `kinds` query 过滤，15s heartbeat，disconnect 时 unregister）
- [x] `agent_bridge/src/agent_bridge/dev/__init__.py`：占位 + 子包 docstring
- [x] `agent_bridge/src/agent_bridge/dev/fire_source.py`：`/dev/fire-source` POST 端点（按 source_name 找 EventSource → fire_now()；404 / 400 / 503 兜底）
- [x] `agent_bridge/src/agent_bridge/app.py`：lifespan 启停 `agent_runtime`（startup `start()` / shutdown `stop()`）；挂 `push_router`；按 `settings.dev_mode` 挂 `dev_fire_router`
- [x] **side-effect**：`agent/src/agent/sessions/manager.py` `SessionManager.start_conversation` 加 backward-compat 可选参数 `tool_hook_invoker` + `post_turn_external`（默认 None / False 保持 002~013 行为）
- [x] `agent_bridge/tests/test_push_channel.py`：6 测试（503 / 400 / encoder format / unicode / 路由挂载 / health 不退化）—— AC-7 结构 + 边界层
- [x] `agent_bridge/tests/test_dev_endpoint.py`：6 测试（dev_mode=False 404 / 422 missing param / 404 unknown / 400 no fire_now / 200 fire_now 触发 / 503 no runtime）—— AC-3 边端 + 安全
- [x] **务实妥协**：SSE chunk-level 内容流测试（"fan_out → SSE 内容到达 client"）**跳过**——httpx AsyncClient + ASGITransport 对长 SSE generator 的 chunk 流式投递不可靠（buffer 到 disconnect 才 flush）；同步 TestClient.stream + iter_lines 同样问题。核心机制（fan_out_event + thread→asyncio bridge + envelope 编码）已由 M14.3 `test_runtime_dispatch` 用 side event loop 真实验证；M14.8 用真起 bridge + dev CLI 端到端验证补足。
- [x] 全 agent_bridge 套件 23 测试全绿（既有 11 + 新 12）；全 agent 套件 183 测试不退化（SessionManager 参数加性扩展零退化）

### M14.6 Pull 路径镜像复制
- [x] `agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py`：`encode_stream` 加可选 `agent_runtime: AgentRuntime | None = None` 参数；每 yield ConversationEvent 同步喂 `fan_out_event(UserEvent(session_id=conv.session.session_id, ...), ev)`；镜像失败 try/except log warning 不打断 pull 流
- [x] `agent_bridge/src/agent_bridge/protocols/ag_ui/routes.py`：`/ag-ui/run` 调 `encode_stream` 时透传 `agent_runtime=runtime.agent_runtime`
- [x] `agent_bridge/src/agent_bridge/protocols/openai/encoders.py`：`encode_streaming` + `encode_nonstreaming` 同上改造
- [x] `agent_bridge/src/agent_bridge/protocols/openai/routes.py`：路由调用透传 `agent_runtime`
- [x] `agent_bridge/tests/test_push_mirror.py`：3 测试（POST /ag-ui/run + 侧 event loop subscriber 真实收到 envelope 含 TextDelta + TurnDone / agent_turn 订阅者不被 user_turn 干扰 / agent_runtime=None 时 pull 路径行为零退化）—— AC-7 真实端到端层（TestClient 同步 post 把 encoder generator 跑完，fan_out 同步喂 subscriber 队列）
- [x] 全 agent_bridge 套件 26 测试全绿（M14.5 23 + 新 3）；agent 183 测试零退化（encoder 加性参数）

### M14.7 Dev CLI + packaging + scripts
- [ ] `agent_bridge/src/agent_bridge/dev/push_subscribe.py`：dev CLI（订阅 /push/subscribe 美化打印）
- [ ] `agent_bridge/pyproject.toml`：`[tool.hatch.build.targets.wheel].exclude` 加 `src/agent_bridge/dev`
- [ ] `scripts/dev-push-subscribe/run.sh` + `run.ps1`：双端 wrapper
- [ ] `scripts/dev-fire-source/run.sh` + `run.ps1`：双端 wrapper
- [ ] `scripts/README.md`：登记两个新脚本

### M14.8 验证
- [ ] `./scripts/check` 全绿（ruff + mypy 全文件 + 全部 pytest 含本期新增）
- [ ] 手工真跑一次：起 bridge `dev_mode=true` + `enable_bedtime=true` + `bedtime_hour=立即`（按 llm-api-confirm 授权）→ 另一端跑 `scripts/dev-push-subscribe/run.sh` → 看到 bedtime envelope 落到 CLI；触发 `/dev/fire-source?source_name=idle_reflection` → memory_observation event 落 session JSONL + memory.observe 被调（log 可见）
- [ ] AC-1 ~ AC-8 全部对应通过

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-12 | M14.1 Session schema 加性扩展 | `events.py` Literal + frozenset 加 `system_trigger` / `memory_observation`；docstring 加 014 段约定；`SCHEMA_VERSION` 不递增（沿用 005/007/009 "纯加性"约定）；既有 10 个 sessions 单测全绿 |
| 2026-06-12 | M14.2 Conversation 内部新增 + 单测 | `Conversation.__init__` 加 2 个 backward-compat 可选参数；`dispatch_system_turn` 公共入口含 user / memory_only 两路径；silent turn 自构 fragment 走 §6 方案 F（speaker=agent 1 utterance + source_ref 关联 memory_observation event uuid）；`_invoke_tool_safely` 接受 ToolHookInvoker 注入；`stream` finally 按 `post_turn_external` 切换；2 个新单测文件 10 个 case 全绿；全 agent 套件 133 个测试全绿（既有 123 + 新增 10），既有路径零退化 |
| 2026-06-12 | M14.3 AgentRuntime 核心 + 5 测试 | 新建 `agent/src/agent/runtime/` 子包 4 个模块（`inbox.py` AgentEvent / `hooks.py` HookRegistry 含四点位+短路+错误隔离 / `listeners.py` ListenerRegistry 含 turn 边界打包+thread→asyncio bridge / `__init__.py` AgentRuntime 含 dispatch loop+默认 PostTurn observe hook+tool_hook_invoker 桥接）；5 个新测试文件 34 个 case 全绿；全 agent 套件 167 测试全绿（既有 133 + 新 34）；AC-1/AC-2/AC-3/AC-4/AC-5 runtime 层/AC-6 runtime 层 全部覆盖；唯一工程妥协是用 `_dispatch` 直接驱动而非 start()/stop() 真线程做 unit 测试，避免线程计时 flakiness（线程语义由 stop 哨兵 + Event.wait 设计保证） |
| 2026-06-12 | M14.4 EventSource 实例 + 单测 | 新增 `agent/src/agent/runtime/sources.py`：UserSource(no thread, submit 入队) + BedtimeSource(独立 thread + schedule loop) + IdleReflectionSource(silent turn, 读 runtime.last_dispatch_finished_at) + `_seconds_until_time_of_day` 纯函数；构造期参数校验 + fire_now 即时触发 + Event.wait 可中断；now_factory 注入便于测试；16 个新测试 case 全绿含 2 个真线程 loop 验证（BedtimeSource 0.1s 内 fire / IdleReflectionSource 超阈值即时 fire / stop 1s 内中断）；全 agent 套件 183 测试全绿（M14.3 167 + 新 16） |
| 2026-06-12 | M14.5 Bridge 集成 + 12 测试 | settings.py 加 8 个 014 字段（dev_mode/enable_bedtime/idle/...）；agent_runtime_factory.py 新文件含 build_agent_runtime（conversation_factory 闭包注入 post_turn_external=True + tool_hook_invoker）；assembly.py BridgeRuntime 加 agent_runtime 字段；push/ 子包（protocol.py encode_envelope_sse 含 unicode + /push/subscribe GET 长 SSE 端点 含 heartbeat/kind 过滤/disconnect 反注册）；dev/ 子包（fire_source.py POST /dev/fire-source 仅 dev_mode 时挂）；app.py lifespan 启停 agent_runtime + 按 dev_mode 挂 dev router；SessionManager.start_conversation 加 backward-compat 可选参数 tool_hook_invoker + post_turn_external；6 push + 6 dev 测试全绿（结构/边界/encoder 内容；chunk-level SSE 流式投递 ASGITransport 限制下跳过，留 M14.8 真跑验证）；agent_bridge 23 测试全绿（既有 11 + 新 12），agent 183 测试零退化 |
| 2026-06-12 | M14.6 Pull 路径镜像复制 + 3 测试 | ag_ui + openai encoder 加可选 `agent_runtime` 参数；每 yield ConversationEvent 同步喂 `fan_out_event`（用 conv.session.session_id 作 UserEvent.session_id）；镜像失败 try/except log warning 不打断；ag_ui + openai routes 调 encoder 时透传 runtime.agent_runtime；test_push_mirror.py 用 TestClient 同步 POST + 侧 event loop subscriber 真实收到 envelope 含 TextDelta + TurnDone 验证完整链路；agent_bridge 26 测试全绿（既有 23 + 新 3），agent 183 测试零退化 |

---

## 验收小结

> AC 标号对应 [`requirement.md` §6](./requirement.md#6-验收标准)。

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1 | main loop dispatch 可跑通 | 🟡 runtime 层覆盖 | M14.3 test_runtime_dispatch（_dispatch 直接驱动）；M14.8 手工跑真起 thread 端到端 |
| AC-2 | Hook 四点位可注册可触发 | ✅ | M14.3 test_runtime_hooks 12 case（含错误隔离） |
| AC-3 | Pre-\* 短路语义可用 | ✅ | M14.3 test_runtime_hooks（PreTurn SKIP + PreToolUse BLOCK）+ test_runtime_dispatch.test_pre_turn_skip_aborts_dispatch / test_tool_hook_invoker_bridges_pre_tool_use_block |
| AC-4 | `_observe_turn` 迁移行为零退化 | ✅ | M14.3 test_runtime_observe_migration（字段清单 + `==` 兜底；含 assistant 多段文本场景） |
| AC-5 | BedtimeSource demo 端到端 | ✅ runtime + source 层 | M14.3 test_runtime_dispatch.test_dispatch_system_trigger_* + M14.4 test_runtime_sources（5 BedtimeSource case 含真 thread schedule loop）；M14.8 手工跑真启 bridge 端到端 |
| AC-6 | IdleReflectionSource silent turn 端到端 | ✅ runtime + source 层 | M14.2 test_conversation_dispatch_system_turn + M14.3 test_runtime_silent_turn + M14.4 test_runtime_sources（5 IdleReflection case 含真 thread loop）；M14.8 手工跑真启 bridge 端到端 |
| AC-7 | Bridge push 通道端到端 | ✅ 结构层 + 镜像链路 | M14.5 test_push_channel（路由 / 状态码 / encoder content） + test_dev_endpoint（dev fire 真触发） + M14.6 test_push_mirror（pull /ag-ui/run 真镜像送达 subscriber，含 TextDelta + TurnDone）；chunk-level 长 SSE 流式留 M14.8 真起 bridge + dev CLI 端到端验证 |
| AC-8 | Dev CLI 落地 | ⏳ | M14.7 双端 wrapper + scripts/README + `./scripts/check` 全绿 |
