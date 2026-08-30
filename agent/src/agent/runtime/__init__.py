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
from collections import OrderedDict
from collections.abc import Callable, Collection
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from memory import Memory

from ..conversation import Conversation
from ..conversation_events import TurnDone
from ..latency import RunLatencyContext, log_run_latency
from ..memory_feed import project_post_turn_observation
from ..sessions.changes import reset_session_mutation_context, set_session_mutation_context
from ..system_prompt.sections import PromptChannel
from ..system_prompt.surfaces import InteractionSurfaceId
from ..tools import ToolResult
from .errors import MissingTurnTerminalError, SessionBusyError, run_failure_reason_code
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
from .identity import RuntimeInstanceIdentity, RuntimeStartReason
from .inbox import AgentEvent, SystemTriggerEvent, UserEvent
from .listeners import (
    ListenerRegistry,
    PushEnvelope,
    RunEventJournalSnapshot,
    RunEventRecord,
    Subscriber,
)
from .models import (
    ConsumerAbandonPolicy,
    ConversationFactory,
    ConversationRunner,
    InteractiveBusyPolicy,
    RunCancelResult,
    RunErrorCallback,
    RunExecutionResult,
    RunHandle,
    RunOutcome,
    RunPriority,
    RunRequest,
    RunSnapshot,
    RunStatus,
    TurnStatus,
)
from .scheduler import RunScheduler, RuntimeActivitySnapshot, RuntimeDrainReport

__all__ = [
    "PRE_TOOL_PROCEED",
    "PRE_TURN_PROCEED",
    "PRE_TURN_SKIP",
    "AgentEvent",
    "AgentRuntime",
    "ConsumerAbandonPolicy",
    "EventSource",
    "HookKind",
    "HookRegistry",
    "InteractiveBusyPolicy",
    "ListenerRegistry",
    "PostTurnContext",
    "PreToolUseDecision",
    "PreTurnDecision",
    "PushEnvelope",
    "RunCancelResult",
    "RunEventJournalSnapshot",
    "RunEventRecord",
    "RunExecutionResult",
    "RunHandle",
    "RunOutcome",
    "RunPriority",
    "RunRequest",
    "RunSnapshot",
    "RunStatus",
    "RuntimeActivitySnapshot",
    "RuntimeDrainReport",
    "RuntimeInstanceIdentity",
    "RuntimeStartReason",
    "SessionBusyError",
    "Subscriber",
    "SystemTriggerEvent",
    "TurnStatus",
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
        max_workers: int = 4,
        max_queued_runs: int = 256,
        max_session_queue: int = 32,
        session_turn_guard: Callable[[str], AbstractContextManager[None]] | None = None,
        recent_terminal_ttl: timedelta = timedelta(minutes=15),
        recent_terminal_limit: int = 512,
        runtime_identity: RuntimeInstanceIdentity | None = None,
    ) -> None:
        if recent_terminal_ttl.total_seconds() <= 0 or recent_terminal_limit <= 0:
            raise ValueError("recent terminal limits must be positive")
        self._conversation_factory = conversation_factory
        self._runtime_identity = runtime_identity or RuntimeInstanceIdentity.create()
        self._memory = memory
        self._inbox: queue.Queue[Any] = queue.Queue()
        self._sources: list[EventSource] = []
        self._hooks = HookRegistry()
        self._listeners = ListenerRegistry(
            runtime_instance_id=self._runtime_identity.bridge_instance_id
        )
        self._run_lock = threading.Lock()
        self._active_run_snapshots: dict[str, RunSnapshot] = {}
        self._recent_terminal_snapshots: OrderedDict[str, RunSnapshot] = OrderedDict()
        self._recent_terminal_ttl = recent_terminal_ttl
        self._recent_terminal_limit = recent_terminal_limit
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_dispatch_finished_at = datetime.now(UTC)
        self._session_turn_guard = session_turn_guard or (lambda _session_id: nullcontext())
        self._scheduler = RunScheduler(
            self._execute_request,
            max_workers=max_workers,
            max_queued_runs=max_queued_runs,
            max_session_queue=max_session_queue,
            on_run_changed=self._handle_run_changed,
        )
        self._register_default_hooks()

    # ----- public API -----

    @property
    def inbox(self) -> queue.Queue[Any]:
        """暴露 inbox 给 EventSource 直接 put（也可在 start 时通过参数注入）。"""
        return self._inbox

    @property
    def runtime_identity(self) -> RuntimeInstanceIdentity:
        return self._runtime_identity

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
            result = ToolResult(
                text=pre.blocked_result_text,
                is_error=True,
                meta={"error_kind": "blocked"},
            )
            self._hooks.run_post_tool_use(name, args, result)
            return result
        result = default_invoke()
        self._hooks.run_post_tool_use(name, args, result)
        return result

    def pre_tool_hook(self, name: str, args: dict[str, Any]) -> ToolResult | None:
        """Run ordered PreToolUse hooks without executing the tool body."""
        decision = self._hooks.run_pre_tool_use(name, args)
        if not decision.block:
            return None
        return ToolResult(
            text=decision.blocked_result_text,
            is_error=True,
            meta={"error_kind": "blocked"},
        )

    def post_tool_hook(self, name: str, args: dict[str, Any], result: ToolResult) -> None:
        """Run ordered PostToolUse hooks after the coordinator accepts a result."""
        self._hooks.run_post_tool_use(name, args, result)

    def start(self) -> None:
        """启动各 EventSource + dispatch thread；idempotent。"""
        if self._thread is not None:
            return None
        self._scheduler.start()
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
            self._scheduler.stop(timeout=timeout)
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
        if not self._thread.is_alive():
            self._thread = None
        else:
            logger.error("AgentRuntime legacy dispatch thread 未在 %.1fs 内停止", timeout)
        if not self._scheduler.stop(timeout=timeout):
            logger.error("AgentRuntime scheduler 未在 %.1fs 内干净停止", timeout)

    def submit(
        self,
        event: AgentEvent,
        *,
        run_id: str | None = None,
        latency_started_ms: int | None = None,
        priority: RunPriority | None = None,
        expires_at: datetime | None = None,
        dedupe_key: str | None = None,
        channel: PromptChannel | None = None,
        surface: InteractionSurfaceId | None = None,
        conversation_factory: ConversationFactory | None = None,
        conversation_runner: ConversationRunner | None = None,
        error_callback: RunErrorCallback | None = None,
        metadata: dict[str, Any] | None = None,
        consumer_abandon_policy: ConsumerAbandonPolicy = ConsumerAbandonPolicy.CANCEL,
        interactive_busy_policy: InteractiveBusyPolicy = InteractiveBusyPolicy.QUEUE,
        control_scope: str | None = None,
    ) -> RunHandle:
        """提交一轮到 session-aware scheduler，返回可流式消费的句柄。"""
        if priority is None:
            priority = (
                RunPriority.INTERACTIVE
                if isinstance(event, UserEvent)
                else RunPriority.PROACTIVE
                if event.output_visibility == "user"
                else RunPriority.BACKGROUND
            )
        kwargs: dict[str, Any] = {
            "event": event,
            "runtime_instance_id": self._runtime_identity.bridge_instance_id,
            "priority": priority,
            "expires_at": expires_at,
            "dedupe_key": dedupe_key,
            "channel": channel,
            "surface": surface,
            "conversation_factory": conversation_factory,
            "conversation_runner": conversation_runner,
            "error_callback": error_callback,
            "metadata": metadata or {},
            "consumer_abandon_policy": consumer_abandon_policy,
            "interactive_busy_policy": interactive_busy_policy,
            "control_scope": control_scope,
        }
        if run_id is not None:
            kwargs["run_id"] = run_id
        if latency_started_ms is not None:
            kwargs["latency_started_ms"] = latency_started_ms
        return self._scheduler.submit(RunRequest(**kwargs))

    def list_run_snapshots(
        self,
        *,
        session_ids: Collection[str] | None = None,
        include_recent_terminal: bool = True,
    ) -> list[RunSnapshot]:
        wanted = set(session_ids) if session_ids is not None else None
        with self._run_lock:
            self._prune_recent_terminal_locked(datetime.now(UTC))
            snapshots = list(self._active_run_snapshots.values())
            if include_recent_terminal:
                snapshots.extend(self._recent_terminal_snapshots.values())
        if wanted is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.session_id in wanted]
        return sorted(snapshots, key=lambda snapshot: (snapshot.created_at, snapshot.run_id))

    def get_run_snapshot(self, run_id: str) -> RunSnapshot | None:
        with self._run_lock:
            self._prune_recent_terminal_locked(datetime.now(UTC))
            return self._active_run_snapshots.get(run_id) or self._recent_terminal_snapshots.get(
                run_id
            )

    def cancel_run(
        self,
        run_id: str,
        *,
        expected_session_id: str,
        required_control_scope: str,
    ) -> tuple[RunCancelResult, RunSnapshot | None]:
        snapshot = self.get_run_snapshot(run_id)
        if snapshot is None:
            return RunCancelResult.NOT_FOUND, None
        if snapshot.session_id != expected_session_id:
            return RunCancelResult.SESSION_MISMATCH, snapshot
        if snapshot.control_scope != required_control_scope:
            return RunCancelResult.SCOPE_MISMATCH, snapshot
        if snapshot.status is RunStatus.TERMINAL:
            return RunCancelResult.ALREADY_TERMINAL, snapshot
        return self._scheduler.cancel_run(
            run_id,
            expected_session_id=expected_session_id,
            required_control_scope=required_control_scope,
        )

    def activity_snapshot(self) -> RuntimeActivitySnapshot:
        return self._scheduler.activity_snapshot()

    def try_reserve_session_mutation(self, session_id: str) -> AbstractContextManager[None]:
        """Reserve an idle session for a short state mutation without queueing it."""

        return self._scheduler.try_reserve_session_mutation(session_id)

    def quiesce(self) -> RuntimeActivitySnapshot:
        return self._scheduler.quiesce()

    def drain(
        self,
        *,
        grace_timeout: float = 5.0,
        cancel_timeout: float = 5.0,
    ) -> RuntimeDrainReport:
        return self._scheduler.drain(
            grace_timeout=grace_timeout,
            cancel_timeout=cancel_timeout,
        )

    # ----- internals -----

    def _handle_run_changed(self, snapshot: RunSnapshot) -> None:
        with self._run_lock:
            if snapshot.status is RunStatus.TERMINAL:
                self._active_run_snapshots.pop(snapshot.run_id, None)
                self._recent_terminal_snapshots[snapshot.run_id] = snapshot
                self._recent_terminal_snapshots.move_to_end(snapshot.run_id)
                self._prune_recent_terminal_locked(snapshot.finished_at or datetime.now(UTC))
            else:
                previous = self._active_run_snapshots.get(snapshot.run_id)
                if previous is not None and previous.version >= snapshot.version:
                    return
                self._active_run_snapshots[snapshot.run_id] = snapshot
        self._listeners.publish_run_change(snapshot)

    def _prune_recent_terminal_locked(self, now: datetime) -> None:
        cutoff = now - self._recent_terminal_ttl
        while self._recent_terminal_snapshots:
            first_run_id, first = next(iter(self._recent_terminal_snapshots.items()))
            if (
                len(self._recent_terminal_snapshots) <= self._recent_terminal_limit
                and first.finished_at is not None
                and first.finished_at >= cutoff
            ):
                break
            self._recent_terminal_snapshots.pop(first_run_id, None)
            self._listeners.drop_run_journal(first_run_id)

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
        fragment = project_post_turn_observation(
            ctx.session,
            turn_start_idx=ctx.turn_start_idx,
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
                # legacy EventSource inbox 也进入新 scheduler；不再占用全局单 dispatch。
                expires_at = None
                dedupe_key = None
                if isinstance(ev, SystemTriggerEvent):
                    dedupe_key = ev.source_kind
                    ttl = ev.event_metadata.get("ttl_seconds")
                    if isinstance(ttl, (int, float)) and ttl > 0:
                        from datetime import timedelta

                        expires_at = datetime.now(UTC) + timedelta(seconds=float(ttl))
                self.submit(
                    ev,
                    expires_at=expires_at,
                    dedupe_key=dedupe_key,
                    metadata={"detached": True},
                )
            except Exception:
                logger.exception("dispatch failed for %r", ev)

    def _execute_request(
        self,
        request: RunRequest,
        handle: RunHandle,
    ) -> RunExecutionResult | None:
        """Scheduler worker 执行一轮；terminal 由 Scheduler finish-once。"""
        with self._session_turn_guard(request.session_id):
            return self._execute_request_guarded(request, handle)

    def _execute_request_guarded(
        self,
        request: RunRequest,
        handle: RunHandle,
    ) -> RunExecutionResult | None:
        """Execute one run while the caller holds the persistent session guard."""
        decision = self._hooks.run_pre_turn(request.event)
        if decision.skip:
            handle.finish_once(RunOutcome.SKIPPED, reason_code=decision.reason or "pre_turn_skip")
            self._listeners.finish_run(
                request.event,
                run_id=request.run_id,
                outcome=RunOutcome.SKIPPED,
                reason_code=decision.reason or "pre_turn_skip",
            )
            return None

        origin = request.metadata.get("protocol")
        if isinstance(request.event, SystemTriggerEvent):
            origin = "system_trigger"
        mutation_token = set_session_mutation_context(
            origin=origin if isinstance(origin, str) else None,
            channel=request.channel,
        )
        try:
            conv = (
                request.conversation_factory()
                if request.conversation_factory is not None
                else self._conversation_factory(request.session_id)
            )
        except BaseException:
            reset_session_mutation_context(mutation_token)
            raise
        run_latency = RunLatencyContext(
            run_id=request.run_id,
            session_id=request.session_id,
            started_ms=request.latency_started_ms,
        )
        conv.set_run_latency_context(run_latency)
        conv.set_runtime_identity(
            request.runtime_instance_id,
            self._runtime_identity.start_reason.value,
        )
        log_run_latency(
            logger,
            run_latency,
            "conversation_bound",
            origin=origin or "unknown",
            channel=request.channel or "none",
            surface=request.surface or "none",
        )
        session = conv.session
        turn_start_idx = len(session.events)
        outcome = RunOutcome.FAILED
        error: BaseException | None = None
        execution_result: RunExecutionResult | None = None
        try:
            if request.conversation_runner is not None:
                events_iter = request.conversation_runner(conv, request, handle.token)
            elif isinstance(request.event, UserEvent):
                events_iter = conv.stream(
                    request.event.user_input,
                    channel=request.channel,
                    surface=request.surface,
                    interaction_destination_id=(
                        str(request.metadata["interaction_destination_id"])
                        if request.metadata.get("interaction_destination_id")
                        else None
                    ),
                    run_id=request.run_id,
                    cancel_check=handle.token.raise_if_cancelled,
                )
            else:
                events_iter = conv.dispatch_system_turn(
                    source_kind=request.event.source_kind,
                    system_prompt_addendum=request.event.system_prompt_addendum,
                    output_visibility=request.event.output_visibility,
                    presentation=request.event.presentation,
                    tool_mode=request.event.tool_mode,
                    channel=request.channel,
                    surface=request.surface,
                    interaction_destination_id=(
                        str(request.metadata["interaction_destination_id"])
                        if request.metadata.get("interaction_destination_id")
                        else None
                    ),
                    event_metadata=request.event.event_metadata,
                    run_id=request.run_id,
                    cancel_check=handle.token.raise_if_cancelled,
                )
            terminal_event: TurnDone | None = None
            for cev in events_iter:
                handle.token.raise_if_cancelled()
                handle.emit(cev)
                self._listeners.fan_out_event(request.event, cev, run_id=request.run_id)
                if isinstance(cev, TurnDone):
                    terminal_event = cev
            if terminal_event is None:
                silent_completion = (
                    isinstance(request.event, SystemTriggerEvent)
                    and request.event.output_visibility == "memory_only"
                )
                if not silent_completion:
                    raise MissingTurnTerminalError(f"run {request.run_id} ended without TurnDone")
                execution_result = RunExecutionResult(
                    turn_status=TurnStatus.COMPLETED,
                    reason_code="end_turn",
                )
            else:
                execution_result = RunExecutionResult(
                    turn_status=TurnStatus(terminal_event.status),
                    reason_code=terminal_event.stop_reason or "end_turn",
                    total_tool_calls=terminal_event.total_tool_calls,
                    tool_call_items=terminal_event.tool_call_items,
                    model_calls=terminal_event.model_calls,
                    model_continuations=terminal_event.model_continuations,
                    budget_dimension=terminal_event.budget_dimension,
                )
            outcome = (
                RunOutcome.COMPLETED
                if execution_result.turn_status is TurnStatus.COMPLETED
                else RunOutcome.CANCELLED
                if execution_result.turn_status is TurnStatus.INTERRUPTED
                else RunOutcome.FAILED
            )
            return execution_result
        except BaseException as exc:
            error = exc
            from .cancellation import RunCancelled

            outcome = RunOutcome.CANCELLED if isinstance(exc, RunCancelled) else RunOutcome.FAILED
            if outcome is RunOutcome.FAILED and request.error_callback is not None:
                try:
                    request.error_callback(conv, exc, request)
                except Exception:
                    logger.warning("run error callback 失败: %s", request.run_id, exc_info=True)
            raise
        finally:
            terminal_persist_error: BaseException | None = None
            try:
                self._last_dispatch_finished_at = datetime.now(UTC)
                self._hooks.run_post_turn(
                    PostTurnContext(
                        session=session,
                        turn_start_idx=turn_start_idx,
                        event=request.event,
                        run_id=request.run_id,
                        outcome=outcome,
                        error=error,
                    )
                )
                terminal_status: TurnStatus = (
                    TurnStatus.INTERRUPTED
                    if outcome is RunOutcome.CANCELLED
                    else TurnStatus.FAILED
                    if outcome is RunOutcome.FAILED
                    else TurnStatus.COMPLETED
                )
                terminal_status_value: Literal["completed", "interrupted", "failed"] = (
                    "interrupted"
                    if terminal_status is TurnStatus.INTERRUPTED
                    else "failed"
                    if terminal_status is TurnStatus.FAILED
                    else "completed"
                )
                terminal_reason = (
                    run_failure_reason_code(error, default=type(error).__name__)
                    if error is not None
                    else execution_result.reason_code
                    if execution_result is not None
                    else "end_turn"
                )
                try:
                    conv.append_run_terminal_event(
                        run_id=request.run_id,
                        status=terminal_status_value,
                        reason_code=terminal_reason,
                        owner_runtime_instance_id=request.runtime_instance_id,
                    )
                except BaseException as persist_error:
                    logger.exception("run terminal persistence failed: %s", request.run_id)
                    if error is None:
                        terminal_persist_error = persist_error
                        error = persist_error
                        outcome = RunOutcome.FAILED
                        terminal_reason = run_failure_reason_code(
                            persist_error,
                            default="run_terminal_persist_failed",
                        )
                self._listeners.finish_run(
                    request.event,
                    run_id=request.run_id,
                    outcome=outcome,
                    reason_code=terminal_reason,
                )
                if terminal_persist_error is not None:
                    raise terminal_persist_error
            finally:
                log_run_latency(
                    logger,
                    run_latency,
                    "conversation_done",
                    outcome=outcome,
                    error_type=type(error).__name__ if error is not None else None,
                    llm_calls=run_latency.llm_call_count,
                    context_builds=run_latency.context_build_count,
                    tool_batches=run_latency.tool_batch_count,
                    tool_calls=run_latency.tool_call_count,
                    first_text_ms=run_latency.first_text_elapsed_ms,
                )
                conv.set_run_latency_context(None)
                reset_session_mutation_context(mutation_token)

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
                presentation=ev.presentation,
                tool_mode=ev.tool_mode,
                event_metadata=ev.event_metadata,
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
