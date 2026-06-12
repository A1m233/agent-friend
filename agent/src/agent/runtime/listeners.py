"""014 · 主动轮事件 fan-out 给订阅者的注册表（thread→asyncio 桥接）。

:class:`AgentRuntime` 每次 dispatch 时把 :class:`ConversationEvent` 喂给
:class:`ListenerRegistry`，注册表按 turn 边界（看到 :class:`TurnDone`）打包成
:class:`PushEnvelope`，通过 ``asyncio.run_coroutine_threadsafe`` 跨 thread 推送
给每个订阅者的 asyncio 队列。

bridge 的 ``/push/subscribe`` 端点对接订阅者；dev CLI 通过该端点订阅。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.3。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from ..conversation_events import ConversationEvent, TurnDone
from .inbox import AgentEvent, SystemTriggerEvent, UserEvent

logger = logging.getLogger(__name__)


EnvelopeKind = Literal["user_turn", "agent_turn", "heartbeat"]


@dataclass(frozen=True)
class PushEnvelope:
    """推给订阅者的一轮 turn 打包。

    Attributes:
        kind: ``"user_turn"`` = 由 ``UserEvent`` 触发的轮 / ``"agent_turn"`` =
            由 ``SystemTriggerEvent`` 触发的主动轮 / ``"heartbeat"`` = 长 SSE
            keep-alive。
        session_id: 来源 session（heartbeat 时为 ``""``）。
        seq: 同一订阅者视角下的单调递增序号（按订阅者维护，断流可观测）。
        source_kind: 仅 agent_turn 有；user_turn / heartbeat 为 ``None``。
        events: 序列化后的 ConversationEvent 列表（heartbeat 时为 ``[]``）。
    """

    kind: EnvelopeKind
    session_id: str
    seq: int
    source_kind: str | None
    events: list[dict[str, Any]]


def _serialize_conversation_event(cev: ConversationEvent) -> dict[str, Any]:
    """ConversationEvent dataclass → 可 JSON 化 dict（自带 ``type`` discriminator）。"""
    return asdict(cev)


class Subscriber:
    """订阅者：异步队列 + 它所属的 event loop + 接受的 kind 过滤。

    bridge 的 ``/push/subscribe`` handler 构造一个 Subscriber 后调用
    :meth:`ListenerRegistry.register`；handler 内 ``await sub.queue.get()`` 拉
    envelope；disconnect 时调 :meth:`ListenerRegistry.unregister`。
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        accept_kinds: frozenset[str],
        queue_maxsize: int = 256,
        sub_id: str | None = None,
    ) -> None:
        self.id = sub_id or str(uuid4())
        self.queue: asyncio.Queue[PushEnvelope] = asyncio.Queue(maxsize=queue_maxsize)
        self.loop = loop
        self.accept_kinds = accept_kinds


class ListenerRegistry:
    """订阅者注册表 + 按 turn 边界 fan-out。

    线程安全：``register`` / ``unregister`` / ``fan_out_event`` 用同一把 lock
    保护 ``_subs`` / ``_turn_buffers`` 一致性。fan_out 把 envelope 通过
    :func:`asyncio.run_coroutine_threadsafe` 推到订阅者所属 loop——队列满或
    loop closed 时 log warning 后丢弃，**绝不阻塞 dispatch thread**。
    """

    def __init__(self) -> None:
        self._subs: dict[str, Subscriber] = {}
        self._seq: dict[str, int] = {}
        # (sub_id, session_id) → 累积 events，TurnDone 时 flush 成一个 envelope
        self._turn_buffers: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def register(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs[sub.id] = sub
            self._seq[sub.id] = 0

    def unregister(self, sub_id: str) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)
            self._seq.pop(sub_id, None)
            # 清理该订阅者的所有 in-progress turn 累积
            for key in list(self._turn_buffers):
                if key[0] == sub_id:
                    del self._turn_buffers[key]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def fan_out_event(
        self,
        agent_event: AgentEvent,
        cev: ConversationEvent,
    ) -> None:
        """每个 :class:`ConversationEvent` 调一次；累积到 :class:`TurnDone`
        时打包一次性推送给订阅者。

        silent turn（``output_visibility="memory_only"``）的
        :meth:`Conversation.dispatch_system_turn` **不 yield 任何 event**——
        本方法因此自然不会被调，silent turn 对订阅者完全不可见。
        """
        if isinstance(agent_event, UserEvent):
            kind: EnvelopeKind = "user_turn"
            source_kind: str | None = None
        elif isinstance(agent_event, SystemTriggerEvent):
            kind = "agent_turn"
            source_kind = agent_event.source_kind
        else:  # pragma: no cover - future event types
            return

        serialized = _serialize_conversation_event(cev)
        is_turn_done = isinstance(cev, TurnDone)

        with self._lock:
            for sub in list(self._subs.values()):
                if kind not in sub.accept_kinds:
                    continue
                key = (sub.id, agent_event.session_id)
                buf = self._turn_buffers.setdefault(key, [])
                buf.append(serialized)
                if is_turn_done:
                    self._seq[sub.id] += 1
                    envelope = PushEnvelope(
                        kind=kind,
                        session_id=agent_event.session_id,
                        seq=self._seq[sub.id],
                        source_kind=source_kind,
                        events=list(buf),
                    )
                    del self._turn_buffers[key]
                    self._safe_push(sub, envelope)

    def _safe_push(self, sub: Subscriber, env: PushEnvelope) -> None:
        """跨 thread 推 envelope；任何错误 log 后丢弃，不抛、不阻塞。"""
        try:
            asyncio.run_coroutine_threadsafe(sub.queue.put(env), sub.loop)
        except RuntimeError:
            # loop closed 或其他 loop 状态异常
            logger.warning(
                "push envelope to subscriber %s 失败 (loop closed?)", sub.id, exc_info=True
            )
        except Exception:
            logger.warning("push envelope to subscriber %s 失败", sub.id, exc_info=True)
