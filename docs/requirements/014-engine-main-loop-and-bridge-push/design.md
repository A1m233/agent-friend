# 014 · 引擎主循环与桥推送通道 — 技术方案

> 对应 [`requirement.md`](./requirement.md)。本文讲"怎么做"：AgentRuntime 怎么套现有 Conversation、EventSource 抽象、Hook 注册执行模型、dispatch_system_turn 与 silent turn 走法、bridge push 通道形态、订阅者生命周期、dev CLI 装配。
>
> 项目级技术栈（Python / uv monorepo / FastAPI / LiteLLM 等）已在 [`0002`](../../decisions/0002-incubation-tech-stack/README.md) 锁定；session 落盘模型与 EventType 加性扩展约定已在 [`002 design`](../002-engine-session-management/design.md) 锁定。本文只讲增量。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 设计目标回顾

把 `Conversation` 的 inner loop 从"唯一驱动源 = user 输入"升级为"事件驱动 outer loop"，且**不重写任何既有代码路径**。两个关键取向贯穿全文：

- **Additive over breaking**：所有改动落在新文件、新类、新 EventType、新 hook 注册口。`Conversation.send` / `stream` / `_build_openai_messages_*` / `_invoke_tool_safely` 等既有方法**一行不动**，作为子例程被 `AgentRuntime` 调用。EventType 走"纯加性"约定（参 [`002 design`](../002-engine-session-management/design.md) §4.2.4 / `events.py:50`），新增 `system_trigger` / `memory_observation` 两个 type 不递增 `SCHEMA_VERSION`。
- **Thread-based + thread-safe queue**：`Conversation.stream()` 是同步生成器（`conversation.py:248`）；改 async 破坏面太大。AgentRuntime 跑在独立 thread、用 `queue.Queue` 做 inbox；EventSource 各自 thread；bridge SSE 端用 `asyncio.run_coroutine_threadsafe` 做 thread→asyncio 桥接（FastAPI 生态熟门模式）。

---

## 2. 整体改动地图

```mermaid
flowchart LR
  subgraph sources["EventSource (各自 thread)"]
    US[UserSource]
    BS[BedtimeSource]
    IS[IdleReflectionSource]
  end
  subgraph runtime["AgentRuntime (独立 thread)"]
    INBOX[(queue.Queue inbox)]
    DISP[dispatch loop]
    HOOKS["Hook chains<br/>pre_turn / post_turn<br/>pre_tool_use / post_tool_use"]
    LIS[(listeners[])]
  end
  subgraph conv["Conversation (既有,不改)"]
    SEND[send/stream]
    DST[dispatch_system_turn ← 新增]
    INNER[inner loop]
  end
  subgraph bridge["agent_bridge (FastAPI loop)"]
    PULL["/ag-ui/run<br/>/v1/chat/completions"]
    PUSH["/push/subscribe ← 新增"]
    DEVFIRE["/dev/fire-source ← 新增<br/>仅 dev_mode"]
    SUBQ[(asyncio.Queue per-subscriber)]
  end

  US --> INBOX
  BS --> INBOX
  IS --> INBOX
  INBOX --> DISP
  DISP --> HOOKS
  HOOKS --> SEND
  HOOKS --> DST
  SEND --> INNER
  DST --> INNER
  DISP --> LIS
  LIS -.thread→asyncio.-> SUBQ
  SUBQ --> PUSH
  PULL -.User 触发轮也镜像.-> LIS
  DEVFIRE -.立即触发.-> BS
  DEVFIRE -.立即触发.-> IS
```

涉及文件清单：

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `agent/src/agent/runtime/__init__.py` | **新文件** | `AgentRuntime` / `AgentEvent` / `EventSource` 协议 / Hook 类型 / 默认 hook 装配 |
| `agent/src/agent/runtime/inbox.py` | **新文件** | `AgentEvent` discriminated union（UserEvent / SystemTriggerEvent） |
| `agent/src/agent/runtime/hooks.py` | **新文件** | `HookKind` / `PreTurnDecision` / `PreToolUseDecision` / 注册 + 调用 + 错误隔离 |
| `agent/src/agent/runtime/sources.py` | **新文件** | `UserSource` / `BedtimeSource` / `IdleReflectionSource`（各自 thread 封装） |
| `agent/src/agent/runtime/listeners.py` | **新文件** | 主动轮事件 fan-out 给订阅者的注册表 |
| `agent/src/agent/sessions/events.py` | 修改（additive） | `EventType` + `ALLOWED_EVENT_TYPES` 加 `system_trigger` / `memory_observation` |
| `agent/src/agent/conversation.py` | 修改（additive） | 新增 `dispatch_system_turn` 公共入口、`_append_system_trigger_event` / `_append_memory_observation_event` / `_observe_turn_at` 私有助手；`_observe_turn` 内部实现移到 `_observe_turn_at(start_idx)`，原方法保留以维持向后兼容签名 |
| `agent/src/agent/memory_feed.py` | **不动** | `project_turn` 行为零变；silent turn 由 `dispatch_system_turn` 自构 fragment（speaker="agent"），不经 project_turn |
| `agent_bridge/src/agent_bridge/assembly.py` | 修改 | `BridgeRuntime` 加 `agent_runtime: AgentRuntime` 字段；`build_runtime()` 装配 `AgentRuntime` + 注册默认 PostTurn hook + 装配 sources |
| `agent_bridge/src/agent_bridge/agent_runtime_factory.py` | **新文件** | 按 `BridgeSettings` 装配 `AgentRuntime`、默认 hook、EventSource 实例 |
| `agent_bridge/src/agent_bridge/settings.py` | 修改（additive） | 加 `dev_mode: bool = False`、`bedtime_hour: int = 23`、`idle_minutes: int = 30`、`enable_bedtime / enable_idle_reflection: bool` |
| `agent_bridge/src/agent_bridge/app.py` | 修改 | `_make_lifespan` 启停 `agent_runtime`；`create_app` 按 `settings.dev_mode` 挂 `/dev/fire-source`；挂 `/push/subscribe` |
| `agent_bridge/src/agent_bridge/push/__init__.py` | **新文件** | push 通道 router；per-subscriber asyncio.Queue；envelope 编码 |
| `agent_bridge/src/agent_bridge/push/protocol.py` | **新文件** | push envelope dataclass + SSE 序列化 |
| `agent_bridge/src/agent_bridge/dev/__init__.py` | **新文件** | dev 子包占位 |
| `agent_bridge/src/agent_bridge/dev/push_subscribe.py` | **新文件** | dev CLI：订阅 `/push/subscribe` 美化打印 |
| `agent_bridge/src/agent_bridge/dev/fire_source.py` | **新文件** | dev 端点 router（fire BedtimeSource / IdleReflectionSource 立即触发） |
| `agent_bridge/pyproject.toml` | 修改 | wheel 排除 `src/agent_bridge/dev` |
| `agent_bridge/protocols/ag_ui/encoders.py` | 修改 | encoder 完成一轮事件 yield 后镜像复制给 push listener（即 `user_turn` envelope） |
| `agent_bridge/protocols/openai/encoders.py` | 修改 | 同上 |
| `scripts/dev-push-subscribe/run.sh` + `run.ps1` | **新文件** | 双端 wrapper 启动 dev CLI 订阅 |
| `scripts/dev-fire-source/run.sh` + `run.ps1` | **新文件** | 双端 wrapper 触发某 source 立即发火 |
| `scripts/README.md` | 修改 | 登记 2 个新脚本 |
| `agent/tests/test_runtime_*.py` | **新文件** | AgentRuntime / EventSource / Hook / dispatch_system_turn / silent turn 单测 |
| `agent_bridge/tests/test_push_channel.py` | **新文件** | push 通道 e2e（参 `test_meta_channel_routes.py` fixture 模式） |

> **关键取舍 · "additive only"**：所有既有字段、方法签名、调用顺序保持原样。`_observe_turn` 的实现被搬到 `_observe_turn_at(start_idx)`，老 `_observe_turn` 保留但 deprecated（实际改为 `AgentRuntime` 注册的默认 PostTurn hook 调 `_observe_turn_at`）。这样 `Conversation.stream()` finally 块即使不动也能继续跑（旧硬编码 + 新 hook 同时存在时，hook 内部 idempotency 兜底——detail 见 §7）。

---

## 3. AgentRuntime + EventSource 协议

### 3.1 AgentEvent discriminated union

`agent/src/agent/runtime/inbox.py`：

```python
@dataclass(frozen=True)
class UserEvent:
    session_id: str
    user_input: str
    type: Literal["user"] = "user"

@dataclass(frozen=True)
class SystemTriggerEvent:
    session_id: str
    source_kind: str                  # "cron:bedtime" / "idle_reflection" / ...
    system_prompt_addendum: str       # 注入到 system message 末尾的引导话
    output_visibility: Literal["user", "memory_only"]
    event_metadata: dict[str, Any] = field(default_factory=dict)
    type: Literal["system_trigger"] = "system_trigger"

AgentEvent = UserEvent | SystemTriggerEvent
```

> **关键取舍 · session_id 在 event 上**：AgentRuntime 是进程级单例、可能跨 session 跑多种触发，inbox 事件需要带 session 路由。dispatch 时按 session_id 通过 `SessionManager.open(...)` 取 `Conversation` 跑。

### 3.2 EventSource 协议

`agent/src/agent/runtime/sources.py`：

```python
class EventSource(Protocol):
    name: ClassVar[str]
    def start(self, inbox: queue.Queue[AgentEvent]) -> None: ...
    def stop(self) -> None: ...
```

- 各 source 收到 `start(inbox)` 后**自行起 thread**（`threading.Thread(daemon=True)`）做工作（cron 定时、idle 计时、HTTP server 处理 user 输入等）。
- `stop()` 同步等 thread join（带超时兜底）。
- `name` 是 source 标识，用于 telemetry / dev fire endpoint 寻址。

### 3.3 AgentRuntime 主循环

`agent/src/agent/runtime/__init__.py`：

```python
class AgentRuntime:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        memory: Memory | None,
    ) -> None:
        self._session_manager = session_manager
        self._memory = memory
        self._inbox: queue.Queue[AgentEvent] = queue.Queue()
        self._sources: list[EventSource] = []
        self._hooks = HookRegistry()
        self._listeners = ListenerRegistry()    # see §8
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._register_default_hooks()

    def register_source(self, src: EventSource) -> None: ...
    def register_hook(self, kind: HookKind, callback) -> None: ...
    def add_listener(self, callback) -> ListenerHandle: ...    # §8

    def start(self) -> None:
        for src in self._sources:
            src.start(self._inbox)
        self._thread = threading.Thread(target=self._run, daemon=True, name="AgentRuntime")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        # 塞一个哨兵唤醒 inbox.get
        self._inbox.put(_SENTINEL)
        for src in self._sources:
            src.stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            ev = self._inbox.get()         # 阻塞
            if ev is _SENTINEL:
                continue
            try:
                self._dispatch(ev)
            except Exception:
                logger.exception("dispatch failed for %r", ev)

    def _dispatch(self, ev: AgentEvent) -> None:
        # PreTurn hook 短路
        decision = self._hooks.run_pre_turn(ev)
        if decision is PreTurnDecision.SKIP:
            return
        session = self._session_manager.open(ev.session_id)
        conv = self._session_manager.bind_conversation(session)
        if isinstance(ev, UserEvent):
            events_iter = conv.stream(ev.user_input)
        else:
            events_iter = conv.dispatch_system_turn(
                source_kind=ev.source_kind,
                system_prompt_addendum=ev.system_prompt_addendum,
                output_visibility=ev.output_visibility,
            )
        turn_start_idx = len(session.events)        # 落入第一条 event 之前的位置
        for cev in events_iter:
            self._listeners.fan_out_event(ev, cev)   # 主动轮 & user 轮都广播；silent turn 不 yield
        self._hooks.run_post_turn(
            PostTurnContext(session=session, turn_start_idx=turn_start_idx, event=ev)
        )
```

> **关键取舍 · session_manager 与 Conversation 装配**：复用 `SessionManager`（assembly.py:172 已有 `runtime.persistent_session_manager`），由它负责 in-memory `Session` cache + per-session `Conversation` 实例。**禁止**在 dispatch 里 new `Conversation`——会引出"同一 session 两个 Conversation 同时操作 events"的竞态。

> **关键取舍 · 单消费者串行**：`_run` 是单 thread + 串行 dispatch（满足 R-4.1.1）。inbox 是 thread-safe 但不需要 lock——队列本身的语义就是单消费者拉的。如果未来要并发跑多 session，再做 per-session inbox 分片，本期不做。

### 3.4 PreTurn 短路 + tool 边界 hook 切入

PreToolUse / PostToolUse 不能由 `AgentRuntime` 直接拦——它们在 `Conversation._invoke_tool_safely` 内部（`conversation.py:742`）。方案：

- `Conversation.__init__` 增加可选参数 `tool_hook_invoker: ToolHookInvoker | None = None`
- `ToolHookInvoker` 是个轻量协议：`def invoke(self, name, args, default_invoke) -> ToolResult`
- `_invoke_tool_safely` 改为：if `self._tool_hook_invoker is not None`: return `self._tool_hook_invoker.invoke(name, args, lambda: self._tool_registry.invoke(name, args))`
- `AgentRuntime._dispatch` 在 bind conversation 时把 `ToolHookInvoker` 注入；invoker 内部跑 pre_tool_use hook → 如 `BLOCK` 直接构造 `ToolResult(text="...", is_error=True)`；否则调 default_invoke + 跑 post_tool_use hook。

老路径（直接 `Conversation()` 构造、不经 AgentRuntime）`tool_hook_invoker=None`，行为零变化——`_invoke_tool_safely` 走原逻辑。

> **关键取舍 · 为什么不在 conversation.py 里直接挂 hook registry**：避免 conversation 跟 hooks 模块循环依赖（hooks 已经依赖 conversation 的 turn context）。轻量协议 + AgentRuntime 注入是更干净的解耦。

---

## 4. EventSource 实例

### 4.1 UserSource

`agent/src/agent/runtime/sources.py`：

```python
class UserSource:
    """Adapter：把外部"调 send/stream"的请求转成 inbox 事件。

    bridge 调用 send_user_input(session_id, text) → 内部 enqueue UserEvent
    → AgentRuntime dispatch → conv.stream(...) → fan-out 给 listener。

    向 bridge 暴露的 send_user_input 是同步阻塞接口（阻塞直到本轮 dispatch 完成），
    便于 bridge SSE encoder 同步迭代结果——见 §8 镜像复制走法。
    """
    name: ClassVar[str] = "user"

    def __init__(self) -> None:
        self._inbox: queue.Queue[AgentEvent] | None = None

    def start(self, inbox): self._inbox = inbox
    def stop(self): pass    # 无后台 thread

    def submit(self, session_id: str, user_input: str) -> None:
        self._inbox.put(UserEvent(session_id=session_id, user_input=user_input))
```

> **关键取舍 · UserSource 不起独立 thread**：user 输入由 bridge 路由处理器主动调 `submit(...)`、自然在请求 thread 里跑入队，不需要 source 自己拉。所以 `start/stop` 是 no-op，只为符合 `EventSource` 协议。

> **关键取舍 · bridge 既有路由怎么接 UserSource**：本期 `/ag-ui/run` 和 `/v1/chat/completions` 的 encoder 不再直接迭代 `conv.stream(...)`，而是改为 (a) `UserSource.submit(...)` 入队 (b) 从 push listener 拿本轮 events（按 session_id + event id 关联）—— 但这会让 pull 路径同步性变差。**简化方案**：本期 pull 路径仍直接调 `conv.stream`，**但调用前先经过 UserSource 的"事件镜像"** —— 即 encoder 在迭代时把 events 副本也喂给 `listeners.fan_out_event(UserEvent(...), cev)`。AgentRuntime 不真的处理这些 user 触发轮事件（pull 自己跑完了），只做"镜像广播"。代价：UserSource.submit 仅在 dev-only / 测试场景用；生产 user 路径仍是 pull encoder 直驱。这个**简化更保守、且符合 R-4.6.4 "pull 路径不退化"**。

### 4.2 BedtimeSource

```python
class BedtimeSource:
    name: ClassVar[str] = "cron:bedtime"

    def __init__(
        self,
        *,
        session_id: str,         # bedtime 提醒发给哪个 session（v1：固定唯一活跃 session）
        bedtime_hour: int = 23,
        bedtime_minute: int = 0,
        prompt_addendum: str = "现在是约定的休息时间，按你当前 persona 自然说一句提醒。",
    ) -> None: ...

    def start(self, inbox):
        self._inbox = inbox
        self._thread = threading.Thread(target=self._loop, daemon=True, name="BedtimeSource")
        self._thread.start()

    def fire_now(self) -> None:
        """供 dev 端点立即触发；正常路径由 _loop 按时间到点调。"""
        self._inbox.put(SystemTriggerEvent(
            session_id=self._session_id,
            source_kind=self.name,
            system_prompt_addendum=self._prompt_addendum,
            output_visibility="user",
        ))

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait_secs = _seconds_until(self._bedtime_hour, self._bedtime_minute)
            # 不用 sleep(wait_secs) 死等——用 Event.wait(wait_secs) 让 stop 能唤醒
            if self._stop.wait(timeout=wait_secs):
                return
            self.fire_now()
```

### 4.3 IdleReflectionSource

```python
class IdleReflectionSource:
    name: ClassVar[str] = "idle_reflection"

    def __init__(
        self,
        *,
        session_id: str,
        idle_minutes: int = 30,
        runtime: AgentRuntime,         # 用来读"上次 dispatch 完成时间"
        prompt_addendum: str = "基于最近的对话，抽取 1-3 条值得长存的事实写给自己。",
    ) -> None: ...

    def fire_now(self) -> None:
        self._inbox.put(SystemTriggerEvent(
            session_id=self._session_id,
            source_kind=self.name,
            system_prompt_addendum=self._prompt_addendum,
            output_visibility="memory_only",       # ← silent turn 的关键
        ))

    def _loop(self) -> None:
        while not self._stop.is_set():
            last = self._runtime.last_dispatch_finished_at
            elapsed = (now_utc() - last).total_seconds()
            need = self._idle_minutes * 60 - elapsed
            if need <= 0:
                self.fire_now()
                # 触发后等 idle 周期，避免连续狂触
                if self._stop.wait(timeout=self._idle_minutes * 60):
                    return
            else:
                if self._stop.wait(timeout=min(need, 60.0)):
                    return
```

> **关键取舍 · IdleReflectionSource 依赖 AgentRuntime.last_dispatch_finished_at**：让 source 自己读 runtime 的最近 dispatch 时间最简单（runtime 内 thread-safe 维护一个 timestamp）。替代方案是"runtime 在每次 dispatch 后回调通知 idle source"，但 source 要订阅 runtime 会引入循环依赖。读 timestamp 是更松的耦合。

### 4.4 装配 by agent_runtime_factory.py

`agent_bridge/src/agent_bridge/agent_runtime_factory.py`：

```python
def build_agent_runtime(
    *,
    settings: BridgeSettings,
    session_manager: SessionManager,
    memory: Memory | None,
) -> AgentRuntime:
    runtime = AgentRuntime(session_manager=session_manager, memory=memory)
    # 默认 sources（按 settings 开关）
    user_src = UserSource()
    runtime.register_source(user_src)
    if settings.enable_bedtime:
        runtime.register_source(BedtimeSource(
            session_id=settings.bedtime_target_session_id,
            bedtime_hour=settings.bedtime_hour,
        ))
    if settings.enable_idle_reflection:
        runtime.register_source(IdleReflectionSource(
            session_id=settings.idle_target_session_id,
            idle_minutes=settings.idle_minutes,
            runtime=runtime,
        ))
    return runtime
```

`BridgeRuntime.user_source` / `BridgeRuntime.agent_runtime` 暴露给 bridge 路由处理器调 `submit` / `add_listener`。

> **关键取舍 · bedtime/idle 的 target session**：v1 假设单 session 单 user 单设备（沿用 006 §"本机假设"）。`bedtime_target_session_id` 由 settings 显式注入（或 `"primary"` 关键字让 factory 解析为 `SessionManager.primary()`——后者细节由 design 阶段验证 SessionManager 是否有此概念，没有就走显式 id）。多设备 / 多 session 路由不是本期范围。

---

## 5. Hook 体系

`agent/src/agent/runtime/hooks.py`：

```python
class HookKind(StrEnum):
    PRE_TURN  = "pre_turn"
    POST_TURN = "post_turn"
    PRE_TOOL_USE  = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"

@dataclass(frozen=True)
class PreTurnDecision:
    skip: bool
    reason: str = ""
    SKIP: ClassVar[PreTurnDecision]
    PROCEED: ClassVar[PreTurnDecision]
PreTurnDecision.SKIP    = PreTurnDecision(skip=True)
PreTurnDecision.PROCEED = PreTurnDecision(skip=False)

@dataclass(frozen=True)
class PreToolUseDecision:
    block: bool
    blocked_result_text: str = ""
    BLOCK: ClassVar[Callable[[str], PreToolUseDecision]]
    PROCEED: ClassVar[PreToolUseDecision]
# Block(reason) = PreToolUseDecision(block=True, blocked_result_text=reason)

@dataclass(frozen=True)
class PostTurnContext:
    session: Session
    turn_start_idx: int                # 本轮新增 events 的切片起点
    event: AgentEvent                  # 本轮的触发事件

class HookRegistry:
    def register(self, kind: HookKind, cb) -> None: ...
    def run_pre_turn(self, ev: AgentEvent) -> PreTurnDecision: ...
    def run_post_turn(self, ctx: PostTurnContext) -> None: ...
    def run_pre_tool_use(self, name, args) -> PreToolUseDecision: ...
    def run_post_tool_use(self, name, args, result) -> None: ...
```

**短路规则**（按 R-4.3.3）：
- `run_pre_turn` 按注册顺序逐个调；**任一返回 SKIP 即立即返回 SKIP**（后续 hook 不再调用，符合"任一 SKIP 即跳过"语义）。
- `run_pre_tool_use` 同上：任一返回 `BLOCK(reason)` 立即返回该 BLOCK；result 喂回 LLM 时 `ToolResult(text=reason, is_error=True)`。

**错误隔离**（按 R-4.3.2）：每个 callback try/except 包；异常 log.warning(exc_info=True) 不打断后续 hook、不打断 dispatch。Pre-\* 中抛异常视为 `PROCEED`（保守默认）；Post-\* 中抛异常无返回值、继续下一个。

**默认 hook 注册**（`AgentRuntime._register_default_hooks`）：

```python
def _post_turn_observe_memory(ctx: PostTurnContext) -> None:
    if self._memory is None:
        return
    if isinstance(ctx.event, SystemTriggerEvent) and ctx.event.output_visibility == "memory_only":
        return       # silent turn 的 memory 喂入由 dispatch_system_turn 自完成；不重复
    new_events = ctx.session.events[ctx.turn_start_idx:]
    fragment = project_turn(
        new_events,
        session_id=ctx.session.session_id,
        persona_id=ctx.session.current_persona_id or "",
    )
    try:
        self._memory.observe(fragment)
    except Exception:
        logger.warning("observe 本轮对话失败", exc_info=True)
```

行为完全复刻 `Conversation._observe_turn`（`conversation.py:672`）的语义——同 fragment 同入参同异常吞掉模式（满足 R-4.5.2 / AC-4）。

---

## 6. dispatch_system_turn 入口

`agent/src/agent/conversation.py` 新增方法（不动既有方法）：

```python
def dispatch_system_turn(
    self,
    *,
    source_kind: str,
    system_prompt_addendum: str,
    output_visibility: Literal["user", "memory_only"] = "user",
) -> Iterator[ConversationEvent]:
    """系统级触发轮入口（R-4.4）。

    - output_visibility="user": 与 stream 同形——yield TextDelta / TurnDone 等
      ConversationEvent，事件会被订阅者（bridge push subscriber）看到
    - output_visibility="memory_only": silent turn，**只产生 events 但不 yield**
      给外部；LLM 输出文本写 memory_observation event + 自构 fragment 调
      memory.observe；上游 listener 收不到任何 ConversationEvent（避免冒泡到 UI）
    """
    persona_name = self._session.current_persona_name
    persona_id = self._session.current_persona_id
    model_snapshot = self._session.current_model

    # 1. 落 system_trigger event（marker，参 compaction 模式不参与 messages 派生）
    self._append_system_trigger_event(
        source_kind=source_kind,
        system_prompt_addendum=system_prompt_addendum,
        output_visibility=output_visibility,
    )

    # 2. 跑 LLM stream（复用 _build_openai_messages_first_turn + trailing system 注入）
    openai_messages = self._assemble(trailing_system=system_prompt_addendum)
    text_buf: list[str] = []
    for ev in self._llm_client.stream(openai_messages, tools=None):     # 本期 silent / system 触发不开 tool
        if isinstance(ev, LLMTextDelta):
            text_buf.append(ev.text)
            if output_visibility == "user":
                yield TextDelta(text=ev.text)
        elif isinstance(ev, LLMTurnDone):
            self._consume_usage(ev)

    full_text = "".join(text_buf)

    # 3. 按 visibility 分支落事件
    if output_visibility == "user":
        self._append_assistant_event(
            full_text, partial=False,
            persona_name=persona_name, persona_id=persona_id, model=model_snapshot,
        )
        yield TurnDone(stop_reason="end_turn", total_tool_calls=0)
    else:
        # silent turn: 落 memory_observation event + 自构 fragment 喂 memory
        obs_uuid = str(uuid4())
        self._append_memory_observation_event(
            uuid=obs_uuid,
            text=full_text,
            source_kind=source_kind,
            persona_id=persona_id or "",
        )
        if self._memory is not None:
            fragment = ConversationFragment(
                session_id=self._session.session_id,
                utterances=[Utterance(
                    speaker="agent",
                    text=full_text,
                    ts=datetime.now(UTC),
                    source_ref=f"{self._session.session_id}#{obs_uuid}",
                )],
                persona_id=persona_id or "",
                owner_user_id="local",
            )
            try:
                self._memory.observe(fragment)
            except Exception:
                logger.warning("silent turn observe 失败", exc_info=True)
        # 不 yield TurnDone——silent turn 对上游不可见
```

新增私有助手：

```python
def _append_system_trigger_event(self, *, source_kind, system_prompt_addendum, output_visibility) -> None:
    ev = Event(
        type="system_trigger",
        uuid=str(uuid4()),
        ts=datetime.now(UTC),
        payload={
            "source_kind": source_kind,
            "system_prompt_addendum": system_prompt_addendum,
            "output_visibility": output_visibility,
        },
    )
    self._store.append_event(self._session.session_id, ev)
    self._session.append(ev)

def _append_memory_observation_event(self, *, uuid, text, source_kind, persona_id) -> None:
    ev = Event(
        type="memory_observation",
        uuid=uuid,
        ts=datetime.now(UTC),
        payload={
            "text": text,
            "source_kind": source_kind,
            "persona_id": persona_id,
        },
    )
    self._store.append_event(self._session.session_id, ev)
    self._session.append(ev)
```

`Session.messages` 派生属性识别老类型不变；新 `system_trigger` / `memory_observation` **不识别 → 自动不参与 messages**——LLM 上下文天然干净（与 `compaction` 同模式，参 `session.py:252-256`）。

> **关键取舍 · silent turn 自构 fragment 而非走 project_turn**：silent turn 的 memory 喂入只有 1 条 utterance（agent 反思文本），形态固定；走 project_turn 反而绕路（project_turn 会扫 events 切片找 user/assistant message，silent turn 写的是 memory_observation——project_turn 会丢弃它）。直接构造保持 `ConversationFragment` shape 不变，**满足 R-4.5.2 字面与精神**（不"压缩 / 类型化"）。

> **关键取舍 · silent turn 不带 tool**：silent turn 是反思类输出，不该调工具（避免意外副作用 / 长耗时）。本期 `dispatch_system_turn` 内 `tools=None`；未来"主动调研类"主动轮如果需要工具，再加 `tools=...` 参数。

### 6.1 EventType 加性扩展

`agent/src/agent/sessions/events.py`：

```python
EventType = Literal[
    # ... 既有 9 类不变 ...
    "system_trigger",      # ← 新增 (R-4.4 / R-4.2.3)
    "memory_observation",  # ← 新增 (R-4.2.4 silent turn 产物)
]

ALLOWED_EVENT_TYPES: Final[frozenset[str]] = frozenset({
    # ... 既有 9 类不变 ...
    "system_trigger",
    "memory_observation",
})
```

`SCHEMA_VERSION` **不递增**——参 events.py:50 注释关于 "纯加性变更"约定（005/007/009 同模式）。`Session.from_events` / `Session.messages` / `Session.from_dict` 都不需要改：

- `from_events`：仍仅要求首条 `session_meta`；不识别 new type 在派生属性里默认忽略
- `messages`：仅识别 `user_message` / `assistant_message` / `tool_call_*`，new type 自动跳过
- `from_dict`：直接构 Event；ALLOWED_EVENT_TYPES 已加入新 type，from_jsonl 校验通过

老 session 文件无新 type，回放完全兼容。

### 6.2 后续架构调整：trailing_system → trailing_user（021）

014 当年（2026-06-12）`dispatch_system_turn` 第 2 步选用 `trailing_system` 注入路径（即把 `system_prompt_addendum` 包成 `role="system"` 接在 history 末尾发给 LLM），在 M14.8 真跑下表现正常。但 015 端到端真跑 + [`issue 006`](../../issues/006-bedtime-prompt-history-hijack/) 复现暴露：当 session history 末尾是上一轮 assistant（尤其含问号），DeepSeek 把 trailing_system 当"现有对话的额外提示"消化、续写 assistant 上文，**不**触发新一轮 turn 切换。

详见 [`issue 006`](../../issues/006-bedtime-prompt-history-hijack/) 与 [`021 需求`](../021-system-trigger-user-injection/)。021 把注入路径切到 `trailing_user`（user role 是 chat-completions 协议的真正 turn 切换信号），配套 `<system_trigger>` tag 包裹 + `project_identity.md` 加 tag 识别元规则，三层加固缓解归因漂移。

本节 §6 第 2 步的 `self._assemble(trailing_system=system_prompt_addendum)` **已被 021 替换为** `self._assemble(trailing_user=system_prompt_addendum)`；其余步骤（marker 落盘、两条 visibility 分支、自构 fragment 喂 memory）形态完全不变——本节其它描述仍然有效。

---

## 7. _observe_turn 迁移（去硬编码 → PostTurn 默认 hook）

按 R-4.5.1，把 `_observe_turn` 的执行权从 `conversation.py` 内 finally 块（`conversation.py:393`）转移到 `AgentRuntime` 注册的默认 PostTurn hook（§5 末尾的 `_post_turn_observe_memory`）。

**改造步骤**：

1. **抽出实现** `agent/src/agent/conversation.py`：把 `_observe_turn(self, start_idx)` 重命名为 `_observe_turn_at(self, start_idx)`（公开方法，由 PostTurn hook 通过 `ctx.session.events[ctx.turn_start_idx:]` 间接调用——其实更直接：hook 直接调 `project_turn` + `memory.observe`，conversation 不再有 `_observe_turn_at`，**全部逻辑搬到 hook**）。
2. **删除 finally 块的硬编码调用**（`conversation.py:393` 的 `self._observe_turn(turn_start_idx)`）。
3. **兼容直接用 Conversation 不走 AgentRuntime 的旧调用方**：现在 `Conversation` 直接构造的场景（如 `agent-cli`、`agent_bridge` 的 pull 路径还没迁完）失去 _observe_turn——**得让 Conversation 内部仍提供一个"如果没人接 PostTurn 就自己跑"的默认行为**。
   - 实现：`Conversation.__init__` 增加可选参数 `post_turn_external: bool = False`；默认 `False` 时 finally 仍跑硬编码 `_observe_turn`；`AgentRuntime` bind conversation 时设 `post_turn_external=True` 跳过硬编码，由 hook 负责。
   - **效果**：迁移期老路径行为零变化；AgentRuntime 路径走 hook；**只有一处真正跑 memory.observe**（保持 R-4.5.2 字面）。

> **关键取舍 · 为什么不一刀切删 finally**：bridge pull encoder 直接迭代 `conv.stream(...)`（`session_bridge.py:138-151` + `protocols/ag_ui/encoders.py`）；本期不打算改 encoder 走 AgentRuntime（见 §4.1 UserSource 的简化方案）。所以 pull 路径下 `Conversation` 仍是"自驱"形态，必须保留默认 finally 行为。等下个需求做桌面端 Tier 0 时统一切换。

### 7.1 AC-4 字段断言清单

按 R-4.5.2 + AC-4 (a)，迁移前后 `memory.observe` 收到的 `ConversationFragment` 关键字段逐项相等：

| 层级 | 字段 | 比较方式 |
|---|---|---|
| ConversationFragment | session_id | == |
| ConversationFragment | persona_id | == |
| ConversationFragment | owner_user_id | == |
| ConversationFragment | len(utterances) | == |
| Utterance[i] | speaker | == |
| Utterance[i] | text | == |
| Utterance[i] | ts | == |
| Utterance[i] | source_ref | == |
| 整体 | json.dumps(asdict(fragment), sort_keys=True) hash | == |

最后一行作为兜底防漂移（哪天 ConversationFragment / Utterance 加了字段，hash 自动捕获）。

---

## 8. Bridge push 通道

### 8.1 协议形态：长 SSE

**为什么 SSE 不 WebSocket**：
- 现有依赖 `fastapi[standard]` / `ag-ui-protocol` 已自带 SSE 工具链
- WebSocket 需引 `websockets` 包 + 双向语义（本期 push 是单向 agent→桌面）
- 桌面端将来消费 push 通道时跨平台 fetch+ReadableStream 即开即用
- 长 SSE 在 FastAPI 里就是 `StreamingResponse(generator, media_type="text/event-stream")`，路径成熟

### 8.2 Envelope schema

`agent_bridge/src/agent_bridge/push/protocol.py`：

```python
@dataclass(frozen=True)
class PushEnvelope:
    kind: Literal["user_turn", "agent_turn", "heartbeat"]
    session_id: str
    seq: int                       # subscriber 视角下的单调递增序号
    source_kind: str | None        # 仅 agent_turn 有；user_turn / heartbeat 是 None
    events: list[dict[str, Any]]   # 序列化后的 ConversationEvent 列表
    # heartbeat 则 events=[]

def encode_envelope_sse(env: PushEnvelope) -> bytes:
    return f"event: push\ndata: {json.dumps(asdict(env), ensure_ascii=False)}\n\n".encode()
```

事件流编排：每个 turn 结束时打包一次（按 turn 边界打包，不按 ConversationEvent 单独发——这样 subscriber 不会拿到"半截" turn）。

### 8.3 订阅注册表 + thread→asyncio 桥接

`agent_bridge/src/agent_bridge/push/__init__.py`：

```python
@dataclass
class _Subscriber:
    id: str
    queue: asyncio.Queue[PushEnvelope]
    loop: asyncio.AbstractEventLoop
    accept_kinds: frozenset[str]    # 过滤 user_turn / agent_turn

class ListenerRegistry:
    def __init__(self) -> None:
        self._subs: dict[str, _Subscriber] = {}
        self._lock = threading.Lock()
        self._turn_buffers: dict[tuple[str, str], list[dict]] = {}  # (sub_id, session_id) → events 累积

    def register(self, sub: _Subscriber) -> None: ...    # under lock
    def unregister(self, sub_id: str) -> None: ...

    def fan_out_event(self, agent_event: AgentEvent, cev: ConversationEvent) -> None:
        """AgentRuntime 在每个 ConversationEvent 时调；累积到 turn 边界一起发。"""
        kind = "user_turn" if isinstance(agent_event, UserEvent) else "agent_turn"
        with self._lock:
            for sub in self._subs.values():
                if kind not in sub.accept_kinds:
                    continue
                buf = self._turn_buffers.setdefault((sub.id, agent_event.session_id), [])
                buf.append(_serialize_conversation_event(cev))
                if isinstance(cev, TurnDone):
                    env = PushEnvelope(
                        kind=kind,
                        session_id=agent_event.session_id,
                        seq=self._next_seq(sub.id),
                        source_kind=getattr(agent_event, "source_kind", None),
                        events=buf,
                    )
                    del self._turn_buffers[(sub.id, agent_event.session_id)]
                    # thread→asyncio bridge
                    asyncio.run_coroutine_threadsafe(sub.queue.put(env), sub.loop)
```

### 8.4 `/push/subscribe` 端点

```python
@push_router.get("/push/subscribe")
async def push_subscribe(request: Request, kinds: str = "agent_turn,user_turn"):
    accept = frozenset(k.strip() for k in kinds.split(",") if k.strip())
    sub = _Subscriber(
        id=str(uuid4()),
        queue=asyncio.Queue(maxsize=256),
        loop=asyncio.get_running_loop(),
        accept_kinds=accept,
    )
    runtime: AgentRuntime = request.app.state.bridge_runtime.agent_runtime
    runtime.listeners.register(sub)

    async def gen():
        try:
            # 立刻发一条 heartbeat 让客户端确认连接活
            yield encode_envelope_sse(PushEnvelope("heartbeat", "", 0, None, []))
            while True:
                if await request.is_disconnected():
                    return
                try:
                    env = await asyncio.wait_for(sub.queue.get(), timeout=15.0)
                    yield encode_envelope_sse(env)
                except asyncio.TimeoutError:
                    yield encode_envelope_sse(PushEnvelope("heartbeat", "", 0, None, []))
        finally:
            runtime.listeners.unregister(sub.id)

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

### 8.5 Pull 路径镜像复制

`protocols/ag_ui/encoders.py` 和 `protocols/openai/encoders.py` 在 `for ev in conv.stream(...)` 循环里，**每 yield 一个 ConversationEvent 也同步喂一份给 listeners**：

```python
# pseudo
for cev in conv.stream(user_input):
    runtime.listeners.fan_out_event(
        UserEvent(session_id=session.session_id, user_input=user_input),
        cev,
    )
    # ... 原有 encode + yield 逻辑不变
```

这样 push subscriber 在 `accept_kinds` 含 `"user_turn"` 时也能看到 user 触发轮——满足 R-4.6.4 "pull 路径不退化" + R-4.6.2 "kind 标识可由 subscriber 过滤"。

### 8.6 BridgeRuntime 装配

`agent_bridge/src/agent_bridge/assembly.py`：

```python
@dataclass(frozen=True)
class BridgeRuntime:
    # ... 既有字段保持不变 ...
    agent_runtime: AgentRuntime      # ← 新增

def build_runtime(settings: BridgeSettings) -> BridgeRuntime:
    # ... 既有装配不动 ...
    agent_runtime = build_agent_runtime(
        settings=settings,
        session_manager=persistent_session_manager,
        memory=memory,
    )
    return BridgeRuntime(
        # ... 既有字段 ...
        agent_runtime=agent_runtime,
    )
```

`app.py` lifespan：

```python
def _make_lifespan(runtime: BridgeRuntime) -> AsyncContextManager:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.agent_runtime.start()    # ← 新增 startup
        try:
            yield
        finally:
            runtime.agent_runtime.stop(timeout=5.0)    # ← 新增 shutdown
            runtime.close()                # 既有 memory drain
    return lifespan

def create_app(...) -> FastAPI:
    # ... 既有装配 ...
    app.include_router(push_router)
    if settings.dev_mode:
        app.include_router(dev_fire_router)   # ← 仅 dev_mode 挂载
    return app
```

---

## 9. Dev CLI + packaging + scripts

### 9.1 Python 实现

`agent_bridge/src/agent_bridge/dev/push_subscribe.py`：

```python
"""dev CLI：订阅 /push/subscribe，按友好格式打印事件。"""

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--kinds", default="agent_turn,user_turn")
    args = p.parse_args(argv)
    # 用 httpx 流式消费 SSE
    with httpx.stream("GET", f"{args.url}/push/subscribe", params={"kinds": args.kinds}, timeout=None) as r:
        for line in r.iter_lines():
            # 解析 SSE event: ... \n data: {...} 并打印
            ...

if __name__ == "__main__":
    sys.exit(main())
```

`agent_bridge/src/agent_bridge/dev/fire_source.py`：FastAPI router，仅 `dev_mode=True` 时挂载：

```python
@dev_fire_router.post("/dev/fire-source")
async def fire_source(req: Request, source_name: str):
    runtime: AgentRuntime = req.app.state.bridge_runtime.agent_runtime
    src = runtime.find_source_by_name(source_name)
    if src is None or not hasattr(src, "fire_now"):
        raise HTTPException(404, f"no fireable source named {source_name!r}")
    src.fire_now()
    return {"ok": True}
```

### 9.2 Packaging：dev/ 从 wheel 排除

`agent_bridge/pyproject.toml`：

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/agent_bridge"]
exclude = ["src/agent_bridge/dev"]
```

> **关键取舍 · 排除而不挪到顶层**：保持在 `src/agent_bridge/dev/` 下，开发期 `python -m agent_bridge.dev.push_subscribe` 命令路径自然；只在 wheel build 时排除——`uv run python -m agent_bridge.dev.xxx` 仍能跑（uv 直接读 source tree，不走 wheel）。挪到顶层 `agent_bridge/dev/` 会让 `python -m` 路径变成 `dev.push_subscribe`，独立于 agent_bridge 包，反而难维护。

### 9.3 Scripts 双端 wrapper

`scripts/dev-push-subscribe/run.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m agent_bridge.dev.push_subscribe "$@"
```

`scripts/dev-push-subscribe/run.ps1`：

```powershell
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m agent_bridge.dev.push_subscribe @args
```

`scripts/dev-fire-source/run.{sh,ps1}` 同模式调 `curl` / `Invoke-WebRequest` 打 `POST /dev/fire-source?source_name=...`。`scripts/README.md` 加两行登记。

### 9.4 BridgeSettings 加配置

```python
class BridgeSettings(BaseSettings):
    # ... 既有字段不变 ...

    # dev 模式
    dev_mode: bool = False              # 生产环境永不开；仅 dev/test 用

    # bedtime
    enable_bedtime: bool = False
    bedtime_target_session_id: str = ""
    bedtime_hour: int = 23
    bedtime_minute: int = 0

    # idle reflection
    enable_idle_reflection: bool = False
    idle_target_session_id: str = ""
    idle_minutes: int = 30
```

env var 前缀 `AGENT_BRIDGE_`，沿用既有 settings 模式。

---

## 10. 影响分析

### 10.1 上下游影响

| 调用方 / 被调用方 | 影响 |
|---|---|
| `agent-cli`（不走 bridge） | 仍直接构造 `Conversation`，`post_turn_external=False` 默认走老 finally 路径——**行为零变化** |
| `agent-bridge` pull 路径（`/ag-ui/run`、`/v1/chat/completions`） | encoder 增加镜像复制行（fan_out_event 调用）；其余迭代逻辑不变；本期 R-4.6.4 |
| `agent-bridge` meta 路径 | 不动 |
| `memory` 模块 | `memory.observe` 调用契约不变；新增的 silent turn 喂入仍是合法 `ConversationFragment`（与 013 协同硬约束一致） |
| `voice_bridge` | 不动 |
| `frontend` | 不动（桌面端 Tier 0 留下个需求） |
| 老 session JSONL 文件 | `from_jsonl` 校验仅识别加性新 EventType；老文件不含新 type 自然兼容；新文件含新 type 在老代码下也能读（`from_jsonl` 早一步行不通——但本期统一升级 ALLOWED_EVENT_TYPES，所有 reader 同时升级） |

### 10.2 跨平台影响

- **Threading 行为**：Python `threading` + `queue.Queue` 三端（mac/linux/windows）行为一致；daemon thread 在进程退出时被 OS kill，结合 lifespan shutdown 显式 `stop()` 防 resource leak。
- **Scripts 双端**：dev-push-subscribe / dev-fire-source 按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落（满足 R-4.7.3）。
- **SSE 长连接**：Windows 上 IIS / Nginx 反代时需要禁用 buffering（`X-Accel-Buffering: no` 已加）；纯本机 uvicorn 直跑无问题。

### 10.3 风险点

| 风险 | 缓解 |
|---|---|
| **AgentRuntime stop 时 thread 卡死** | inbox 哨兵 + `_stop_evt`，`thread.join(timeout=5.0)` 超时不阻塞 shutdown；每个 EventSource 用 `Event.wait(timeout)` 而非 `time.sleep` 避免不可中断 |
| **silent turn 抢占用户对话** | AgentRuntime 是单消费者串行，user 输入与 silent turn 在同一队列；silent turn 通常耗时数秒，会延后 user 响应。**缓解**：PreTurn hook 可注册 idle source 的"如有 pending user 输入则 SKIP"判断（本期不强求，留 future） |
| **listener fan_out 抛异常 / 阻塞** | `fan_out_event` 内 try/except 包；`asyncio.run_coroutine_threadsafe.put` 返回 future 不 await，写满 queue 时直接抛 `QueueFull` 被 catch & log，避免 dispatch thread 阻塞 |
| **silent turn 的 `assistant_message` 路径未触发** | dispatch_system_turn `memory_only` 分支不调 `_append_assistant_event`——但 LLM 实际产生了 text。design 上明确：silent turn 的文本**只**作为 `memory_observation.payload.text` 落盘 + 直接喂 memory。`session.messages` 派生中绝对看不到这条——历史天然干净 |
| **memory_observation 老 reader 报错** | `from_jsonl` 校验 `ALLOWED_EVENT_TYPES`——本期所有 reader（agent / memory / voice_bridge / memory_eval）会一并升级版本；migration 风险等于"加一行类型"，hatch wheel rebuild + 测试覆盖 |
| **AgentRuntime 单例 vs 测试隔离** | bridge `app.state.bridge_runtime` 每次 `create_app` 注入一份新 runtime；测试可走 `create_app_with_runtime(custom_runtime)`（既有 fixture 模式，参 `test_meta_channel_routes.py:28-69`） |

---

## 11. 测试策略

### 11.1 既有单测预期

| 测试 | 预期 |
|---|---|
| `agent/tests/test_conversation_*.py`（既有） | 全绿。`Conversation` 既有路径行为零变化 |
| `agent_bridge/tests/test_meta_channel_routes.py`（既有） | 全绿。Meta 路由不动 |
| `memory/tests/*`（既有） | 全绿。`Memory.observe` / `Memory.retrieve` 签名 + 调用语义零变化 |

### 11.2 新增单测

| 文件 | 覆盖 |
|---|---|
| `agent/tests/test_runtime_inbox.py` | AgentEvent 序列化、UserEvent / SystemTriggerEvent 字段；inbox put/get；哨兵停机 |
| `agent/tests/test_runtime_hooks.py` | 四点位注册 + 触发；PreTurn SKIP 短路；PreToolUse BLOCK 短路 → ToolResult(is_error=True)；单 hook 抛异常不打断主流程（**AC-2**） |
| `agent/tests/test_runtime_dispatch.py` | UserEvent dispatch 与 conv.stream 同行为；SystemTriggerEvent output_visibility="user" 路径出 assistant_message + TurnDone（**AC-5**） |
| `agent/tests/test_runtime_silent_turn.py` | output_visibility="memory_only" 路径写 memory_observation event + 调 memory.observe(fragment with 1 utterance) + 不冒泡 ConversationEvent + 不写 assistant_message + session.messages 不含 silent text（**AC-6**） |
| `agent/tests/test_runtime_observe_migration.py` | 迁移前后跑同一组 conversation；mock `memory.observe` 拦截 fragment，按 §7.1 字段清单 + hash 兜底 assertAllEqual（**AC-4**） |
| `agent/tests/test_runtime_sources.py` | UserSource.submit；BedtimeSource.fire_now；IdleReflectionSource.fire_now；schedule loop 用 fake clock 验证按时间到点触发 |
| `agent_bridge/tests/test_push_channel.py` | 端到端：起 app with AgentRuntime fixture → 用 httpx async client GET /push/subscribe → 触发 BedtimeSource.fire_now → 校验 envelope 到达 + kind="agent_turn" + source_kind + events 含 TextDelta + TurnDone（**AC-7**） |
| `agent_bridge/tests/test_push_mirror.py` | pull `/ag-ui/run` 与 push subscribe 并发：user_turn envelope 在 push 端按 kind 过滤可见；pull SSE 内容不退化 |
| `agent_bridge/tests/test_dev_endpoint.py` | dev_mode=False 时 `/dev/fire-source` 返 404（router 未挂载）；dev_mode=True 时 POST 触发对应 source.fire_now |

### 11.3 验收支撑

requirement.md §6 的 AC-1 ~ AC-8 与上面新增单测对应关系已在表中行末注明。AC-1（main loop dispatch）由 `test_runtime_dispatch.py` 的 UserEvent 路径覆盖；AC-3（Pre-\* 短路）由 `test_runtime_hooks.py`；AC-8（dev CLI + scripts/README）由 `scripts/check` 在 lint 阶段自检 README 表格存在。

---

## 12. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-12
- **确认时间**：2026-06-12
- **对应需求**：[`requirement.md`](./requirement.md)
- **协同需求**：013 memory pass-1（硬接口契约：`Memory.observe` / `Memory.retrieve` 签名不变、`ConversationFragment` 形状不变；本方案 §5 默认 hook 与 §6 silent turn 自构 fragment 两条路径都满足）
- **下一步**：本文档确认后撰写同目录 `progress.md`（任务清单），进 Phase 3 实施
