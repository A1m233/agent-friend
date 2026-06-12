"""014 · 进程启动期装配 :class:`agent.runtime.AgentRuntime`。

把 :class:`agent.SessionManager` + :class:`memory.Memory` 与 :class:`agent.runtime.AgentRuntime`
连接起来：构造 ``conversation_factory``（每次 dispatch 时通过 SessionManager 找
session + 装配 Conversation 时开启 ``post_turn_external=True`` 并注入
``tool_hook_invoker``），按 :class:`BridgeSettings` 装上启用的 EventSource。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §4.4。
"""

from __future__ import annotations

import logging

from agent.runtime import AgentRuntime
from agent.runtime.sources import (
    BedtimeSource,
    IdleReflectionSource,
    UserSource,
)

from agent import Conversation, SessionManager
from memory import Memory

from .settings import BridgeSettings

logger = logging.getLogger(__name__)


def build_agent_runtime(
    *,
    settings: BridgeSettings,
    session_manager: SessionManager,
    memory: Memory | None,
) -> AgentRuntime:
    """装配 :class:`AgentRuntime`：注入 conversation_factory + 注册启用的 source。

    Returns:
        已注册 source、待 :meth:`AgentRuntime.start` 拉起的 runtime。
        启动 / 停止由 :func:`agent_bridge.app._make_lifespan` 在 FastAPI lifespan
        里完成。
    """
    runtime: AgentRuntime  # forward reference for closure

    def conversation_factory(session_id: str) -> Conversation:
        session = session_manager.open(session_id)
        return session_manager.start_conversation(
            session,
            post_turn_external=True,
            tool_hook_invoker=runtime.tool_hook_invoker,
        )

    runtime = AgentRuntime(
        conversation_factory=conversation_factory,
        memory=memory,
    )

    # UserSource 永远注册（dev / 测试场景可用）；生产 pull 路径不走它
    runtime.register_source(UserSource())

    if settings.enable_bedtime:
        if not settings.bedtime_target_session_id:
            logger.warning(
                "enable_bedtime=True 但 bedtime_target_session_id 为空，BedtimeSource 不会注册"
            )
        else:
            runtime.register_source(
                BedtimeSource(
                    session_id=settings.bedtime_target_session_id,
                    bedtime_hour=settings.bedtime_hour,
                    bedtime_minute=settings.bedtime_minute,
                )
            )

    if settings.enable_idle_reflection:
        if not settings.idle_target_session_id:
            logger.warning(
                "enable_idle_reflection=True 但 idle_target_session_id 为空，"
                "IdleReflectionSource 不会注册"
            )
        else:
            runtime.register_source(
                IdleReflectionSource(
                    session_id=settings.idle_target_session_id,
                    runtime=runtime,
                    idle_minutes=settings.idle_minutes,
                )
            )

    return runtime
