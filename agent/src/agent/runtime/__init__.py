"""014 · AgentRuntime + EventSource 抽象 + 默认 hook 装配。

把 :class:`agent.Conversation` 从"唯一驱动源 = user 输入"升级为"事件驱动 outer
loop"——:class:`AgentRuntime` 跑在独立 thread，从 thread-safe :class:`queue.Queue`
inbox 取事件，按事件类型 dispatch 到 :meth:`Conversation.stream` /
:meth:`Conversation.dispatch_system_turn`，中间穿四点位 Hook
（PreTurn / PostTurn / PreToolUse / PostToolUse）。

设计要点：

- **inner loop 一行不改**：现有 :meth:`Conversation.stream` 作为子例程被
  AgentRuntime 调用；老路径完全保留向后兼容
- **single-consumer dispatch**：单 thread 串行 dispatch，避免并发改 session
- **post_turn_external 切换**：AgentRuntime 装配的 Conversation 关闭 finally 内
  硬编码 ``_observe_turn``，改由 PostTurn 默认 hook 触发（去硬编码）
- **listeners fan-out**：每轮 ConversationEvent 同步喂 ListenerRegistry，按 turn
  边界打包给 bridge push subscribers

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3 / §5 / §7。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol, runtime_checkable

from memory import Memory

from ..conversation import Conversation
from ..memory_feed import project_turn
from ..tools import ToolResult
from .hooks import (
    PRE_TOOL_PROCEED,
    PRE_TURN_PROCEED,
    PRE_TURN_SKIP,
    HookKind,
    HookRegistry,
    PostTurnContext,
    PreToolUseDecision,
    PreTurnDecision,
    pre_tool_block,
)
from .inbox import AgentEvent, SystemTriggerEvent, UserEvent
from .listeners import ListenerRegistry, PushEnvelope, Subscriber

__all__ = [
    "PRE_TOOL_PROCEED",
    "PRE_TURN_PROCEED",
    "PRE_TURN_SKIP",
    "AgentEvent",
    "AgentRuntime",
    "EventSource",
    "HookKind",
    "HookRegistry",
    "ListenerRegistry",
    "PostTurnContext",
    "PreToolUseDecision",
    "PreTurnDecision",
    "PushEnvelope",
    "Subscriber",
    "SystemTriggerEvent",
    "UserEvent",
    "pre_tool_block",
]


logger = logging.getLogger(__name__)


_SENTINEL: Any = object()
"""inbox 哨兵：stop() 时塞一条以唤醒阻塞在 ``inbox.get()`` 的 dispatch loop。"""


@runtime_checkable
class EventSource(Protocol):
    """EventSource 协议（014 R-4.2.1）。

    各 source 收到 :meth:`start` 后自行起 thread / scheduler 把事件塞 inbox；
    :meth:`stop` 同步等所有内部 thread 结束（带超时兜底）。

    Note:
        ``name`` 是 source 标识，用于 telemetry 与 dev fire endpoint 寻址；
        实现类用 :class:`typing.ClassVar` 标注即可。
    """

    name: ClassVar[str]

    def start(self, inbox: queue.Queue[Any]) -> None: ...

    def stop(self) -> None: ...


class AgentRuntime:
    """main loop 调度内核（014 R-4.1）。

    Args:
        conversation_factory: 按 ``session_id`` 取 :class:`Conversation` 实例的工厂。
            典型实现：闭包到 ``SessionManager``，让 conversation 实例可复用。
            **AgentRuntime 不直接持有 Conversation**——避免长生命周期实例的
            session-bind 状态泄露；每次 dispatch 时调 factory。
        memory: 可选 :class:`memory.Memory`，传给默认 PostTurn observe hook。

    Note:
        ``conversation_factory`` 返回的 Conversation **应已设**
        ``post_turn_external=True`` 与适当的 ``tool_hook_invoker``——
        装配代码（``agent_bridge.agent_runtime_factory``）负责
        （014 design §3.4 / §7）。AgentRuntime 不强制校验这一点，但若开发期
        漏配，会出现"_observe_turn 被调两次"等行为偏差。
    """

    def __init__(
        self,
        *,
        conversation_factory: Callable[[str], Conversation],
        memory: Memory | None = None,
    ) -> None:
        self._conversation_factory = conversation_factory
        self._memory = memory
        self._inbox: queue.Queue[Any] = queue.Queue()
        self._sources: list[EventSource] = []
        self._hooks = HookRegistry()
        self._listeners = ListenerRegistry()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_dispatch_finished_at = datetime.now(UTC)
        self._register_default_hooks()

    # ----- public API -----

    @property
    def inbox(self) -> queue.Queue[Any]:
        """暴露 inbox 给 EventSource 直接 put（也可在 start 时通过参数注入）。"""
        return self._inbox

    @property
    def listeners(self) -> ListenerRegistry:
        """订阅者注册表（bridge push handler 通过它 register/unregister 自己）。"""
        return self._listeners

    @property
    def last_dispatch_finished_at(self) -> datetime:
        """最近一次 dispatch 结束时间（供 :class:`IdleReflectionSource` 计算 idle 时长）。"""
        return self._last_dispatch_finished_at

    def register_source(self, src: EventSource) -> None:
        """注册 EventSource；:meth:`start` 时统一拉起。"""
        self._sources.append(src)

    def register_hook(
        self,
        kind: HookKind,
        callback: Callable[..., Any],
    ) -> None:
        """注册 hook callback 到某点位（注册顺序 = 执行顺序）。"""
        self._hooks.register(kind, callback)

    def tool_hook_invoker(
        self,
        name: str,
        args: dict[str, Any],
        default_invoke: Callable[[], ToolResult],
    ) -> ToolResult:
        """注入到 :class:`Conversation` 的 ``tool_hook_invoker`` callable
        （由装配代码绑到 ``conversation_factory``）。

        Workflow：
            1. 跑 PreToolUse hook 链；BLOCK → 构造 ``ToolResult(is_error=True)``
               不调真 tool
            2. 否则调 ``default_invoke()`` 取真 result
            3. 跑 PostToolUse hook 链（旁路观察，不改 result）

        Note:
            BLOCK 路径下 PostToolUse hook 仍会被调（让旁路 hook 看到 BLOCK 结果）。
        """
        pre = self._hooks.run_pre_tool_use(name, args)
        if pre.block:
            result = ToolResult(text=pre.blocked_result_text, is_error=True)
            self._hooks.run_post_tool_use(name, args, result)
            return result
        result = default_invoke()
        self._hooks.run_post_tool_use(name, args, result)
        return result

    def start(self) -> None:
        """启动各 EventSource + dispatch thread；idempotent。"""
        if self._thread is not None:
            return
        for src in self._sources:
            src.start(self._inbox)
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="AgentRuntime")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """停止各 source + dispatch thread；幂等。

        Args:
            timeout: dispatch thread join 的超时时间（秒）。超时不抛——
                daemon thread 会随进程退出，避免阻塞 shutdown。
        """
        if self._thread is None:
            return
        self._stop_evt.set()
        self._inbox.put(_SENTINEL)  # 唤醒阻塞在 inbox.get() 的 dispatch loop
        for src in self._sources:
            try:
                src.stop()
            except Exception:
                logger.warning(
                    "source %r stop 抛异常",
                    getattr(src, "name", "?"),
                    exc_info=True,
                )
        self._thread.join(timeout=timeout)
        self._thread = None

    # ----- internals -----

    def _register_default_hooks(self) -> None:
        """默认 PostTurn hook：把 ``_observe_turn`` 从 conversation.py 硬编码迁过来
        （014 R-4.5）。"""
        self._hooks.register(HookKind.POST_TURN, self._default_post_turn_observe)

    def _default_post_turn_observe(self, ctx: PostTurnContext) -> None:
        """等价于 :meth:`Conversation._observe_turn` 的行为，零退化。

        silent turn（``output_visibility="memory_only"``）的 memory 喂入由
        :meth:`Conversation.dispatch_system_turn` **自完成**（写
        ``memory_observation`` event + 自构 fragment 调 ``memory.observe``）；
        本 hook 跳过 silent turn 以避免重复 observe（防止把 memory_observation
        event 错误地投影成 utterance——其实 :func:`project_turn` 不识别新 type
        会过滤掉，但显式 short-circuit 更稳）。
        """
        if self._memory is None:
            return
        if (
            isinstance(ctx.event, SystemTriggerEvent)
            and ctx.event.output_visibility == "memory_only"
        ):
            return
        new_events = ctx.session.events[ctx.turn_start_idx :]
        fragment = project_turn(
            new_events,
            session_id=ctx.session.session_id,
            persona_id=ctx.session.current_persona_id or "",
        )
        try:
            self._memory.observe(fragment)
        except Exception:
            logger.warning("PostTurn observe 失败", exc_info=True)

    def _run(self) -> None:
        """dispatch loop 主体（独立 thread 里跑）。"""
        while not self._stop_evt.is_set():
            ev = self._inbox.get()
            if ev is _SENTINEL:
                continue
            if not isinstance(ev, (UserEvent, SystemTriggerEvent)):
                logger.warning("AgentRuntime inbox 收到未知事件: %r", ev)
                continue
            try:
                self._dispatch(ev)
            except Exception:
                logger.exception("dispatch failed for %r", ev)
            finally:
                self._last_dispatch_finished_at = datetime.now(UTC)

    def _dispatch(self, ev: AgentEvent) -> None:
        """单条事件的 dispatch：PreTurn → 跑 conv → fan_out → PostTurn。"""
        decision = self._hooks.run_pre_turn(ev)
        if decision.skip:
            return

        conv = self._conversation_factory(ev.session_id)
        session = conv.session
        turn_start_idx = len(session.events)

        if isinstance(ev, UserEvent):
            events_iter = conv.stream(ev.user_input)
        else:
            # SystemTriggerEvent
            events_iter = conv.dispatch_system_turn(
                source_kind=ev.source_kind,
                system_prompt_addendum=ev.system_prompt_addendum,
                output_visibility=ev.output_visibility,
            )

        for cev in events_iter:
            self._listeners.fan_out_event(ev, cev)

        self._hooks.run_post_turn(
            PostTurnContext(
                session=session,
                turn_start_idx=turn_start_idx,
                event=ev,
            )
        )
