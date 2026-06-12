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
- [ ] `agent/src/agent/runtime/__init__.py`：包导出 + `AgentRuntime` 类（含 `_register_default_hooks` / `_dispatch` / `start` / `stop` / `register_source` / `register_hook` / `add_listener` / `last_dispatch_finished_at` property）
- [ ] `agent/src/agent/runtime/inbox.py`：`AgentEvent` discriminated union（UserEvent / SystemTriggerEvent）
- [ ] `agent/src/agent/runtime/hooks.py`：`HookKind` / `PreTurnDecision` / `PreToolUseDecision` / `PostTurnContext` / `HookRegistry`（含短路 + 错误隔离）
- [ ] `agent/src/agent/runtime/listeners.py`：`_Subscriber` / `ListenerRegistry`（fan_out_event + turn 边界打包 + thread→asyncio bridge）
- [ ] `agent/tests/test_runtime_inbox.py`：AgentEvent 字段；put/get；哨兵停机
- [ ] `agent/tests/test_runtime_hooks.py`：四点位注册 + 触发；PreTurn SKIP 短路；PreToolUse BLOCK 短路；单 hook 抛异常不打断其他 hook 与 dispatch（AC-2）
- [ ] `agent/tests/test_runtime_dispatch.py`：UserEvent dispatch 与 conv.stream 同行为；SystemTriggerEvent (user visibility) 路径出 assistant_message + TurnDone（AC-5 一部分）
- [ ] `agent/tests/test_runtime_silent_turn.py`：output_visibility="memory_only" 走 §6 silent 路径，listener 收不到事件（AC-6）
- [ ] `agent/tests/test_runtime_observe_migration.py`：mock memory.observe 拦截 fragment，断言 §7.1 字段清单 + json hash 兜底（AC-4）

### M14.4 EventSource 实例
- [ ] `agent/src/agent/runtime/sources.py`：`UserSource` / `BedtimeSource` / `IdleReflectionSource`
- [ ] BedtimeSource / IdleReflectionSource 内 thread + stop event；fire_now 立即触发；schedule loop 用 `Event.wait(timeout)` 可中断
- [ ] `agent/tests/test_runtime_sources.py`：UserSource.submit；fire_now 直接入队；BedtimeSource schedule loop 用 fake clock 验证按时间到点触发

### M14.5 Bridge 集成
- [ ] `agent_bridge/src/agent_bridge/settings.py`：加 `dev_mode` / `enable_bedtime` / `bedtime_target_session_id` / `bedtime_hour` / `bedtime_minute` / `enable_idle_reflection` / `idle_target_session_id` / `idle_minutes`
- [ ] `agent_bridge/src/agent_bridge/agent_runtime_factory.py`：新文件，`build_agent_runtime(settings, session_manager, memory)`
- [ ] `agent_bridge/src/agent_bridge/assembly.py`：`BridgeRuntime` 加 `agent_runtime: AgentRuntime`；`build_runtime` 装配
- [ ] `agent_bridge/src/agent_bridge/push/protocol.py`：`PushEnvelope` dataclass + `encode_envelope_sse`
- [ ] `agent_bridge/src/agent_bridge/push/__init__.py`：`push_router` + `/push/subscribe` endpoint
- [ ] `agent_bridge/src/agent_bridge/dev/__init__.py`：占位
- [ ] `agent_bridge/src/agent_bridge/dev/fire_source.py`：`dev_fire_router` + `/dev/fire-source`
- [ ] `agent_bridge/src/agent_bridge/app.py`：lifespan 启停 agent_runtime；挂 push_router；按 `settings.dev_mode` 挂 dev_fire_router
- [ ] `agent_bridge/tests/test_push_channel.py`：起 app with fixture → httpx async GET /push/subscribe → 触发 BedtimeSource.fire_now → 校验 envelope kind="agent_turn" + events 含 TextDelta + TurnDone（AC-7）
- [ ] `agent_bridge/tests/test_dev_endpoint.py`：dev_mode=False 时 /dev/fire-source 返 404；dev_mode=True 时 POST 触发对应 source.fire_now

### M14.6 Pull 路径镜像复制
- [ ] `agent_bridge/src/agent_bridge/protocols/ag_ui/encoders.py`：每 yield ConversationEvent 同步喂一份给 `runtime.listeners.fan_out_event(UserEvent(...), cev)`
- [ ] `agent_bridge/src/agent_bridge/protocols/openai/encoders.py`：同上
- [ ] `agent_bridge/tests/test_push_mirror.py`：pull `/ag-ui/run` 与 push subscribe 并发：push subscriber 在 `accept_kinds` 含 `user_turn` 时能收到 envelope；pull SSE 内容不退化

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

---

## 验收小结

> AC 标号对应 [`requirement.md` §6](./requirement.md#6-验收标准)。

| AC | 标题 | 状态 | 依据 |
|---|---|---|---|
| AC-1 | main loop dispatch 可跑通 | ⏳ | M14.3 test_runtime_dispatch + M14.8 手工跑 |
| AC-2 | Hook 四点位可注册可触发 | ⏳ | M14.3 test_runtime_hooks |
| AC-3 | Pre-\* 短路语义可用 | ⏳ | M14.3 test_runtime_hooks（PreTurn SKIP + PreToolUse BLOCK 两类） |
| AC-4 | `_observe_turn` 迁移行为零退化 | ⏳ | M14.3 test_runtime_observe_migration（字段清单 + hash 兜底） |
| AC-5 | BedtimeSource demo 端到端 | ⏳ | M14.3 test_runtime_dispatch + M14.4 test_runtime_sources + M14.8 手工跑 |
| AC-6 | IdleReflectionSource silent turn 端到端 | ⏳ | M14.2 test_conversation_dispatch_system_turn + M14.3 test_runtime_silent_turn + M14.8 手工跑 |
| AC-7 | Bridge push 通道端到端 | ⏳ | M14.5 test_push_channel + M14.6 test_push_mirror + M14.8 手工跑 |
| AC-8 | Dev CLI 落地 | ⏳ | M14.7 双端 wrapper + scripts/README + `./scripts/check` 全绿 |
