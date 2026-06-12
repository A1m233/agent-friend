"""014 · bridge agent→桌面 push 通道（``GET /push/subscribe``）。

长 SSE 端点：客户端 connect → bridge 注册一个 :class:`Subscriber` 到
:class:`agent.runtime.AgentRuntime` 的 listener registry → AgentRuntime
每完成一轮 dispatch 把 envelope 推到 subscriber 的 asyncio.Queue →
本端点把 queue 里的 envelope 编码为 SSE 推给客户端。

15 秒无事件时发一个 heartbeat 防代理掐连接；客户端 disconnect 时反注册。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §8.4。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.runtime import PushEnvelope, Subscriber
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .protocol import encode_envelope_sse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from ..assembly import BridgeRuntime


logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_SECONDS = 15.0


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """挂 push 通道路由（``GET /push/subscribe``）到 FastAPI app。"""
    router = APIRouter()

    @router.get("/push/subscribe")
    async def push_subscribe(
        request: Request,
        kinds: str = Query(
            "agent_turn,user_turn",
            description="逗号分隔的 envelope kind 过滤：agent_turn / user_turn",
        ),
    ) -> StreamingResponse:
        if runtime.agent_runtime is None:
            raise HTTPException(
                status_code=503,
                detail="agent_runtime 未装配——bridge 当前不接受 push 订阅",
            )

        accept_kinds = frozenset(k.strip() for k in kinds.split(",") if k.strip())
        if not accept_kinds:
            raise HTTPException(status_code=400, detail="kinds 不能为空")

        sub = Subscriber(
            loop=asyncio.get_running_loop(),
            accept_kinds=accept_kinds,
        )
        runtime.agent_runtime.listeners.register(sub)

        async def gen() -> AsyncIterator[bytes]:
            try:
                # 立刻发一条 heartbeat 让客户端确认连接活
                yield encode_envelope_sse(_heartbeat())
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        env = await asyncio.wait_for(
                            sub.queue.get(),
                            timeout=HEARTBEAT_INTERVAL_SECONDS,
                        )
                        yield encode_envelope_sse(env)
                    except TimeoutError:
                        yield encode_envelope_sse(_heartbeat())
            finally:
                if runtime.agent_runtime is not None:
                    runtime.agent_runtime.listeners.unregister(sub.id)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(router)


def _heartbeat() -> PushEnvelope:
    return PushEnvelope(
        kind="heartbeat",
        session_id="",
        seq=0,
        source_kind=None,
        events=[],
    )
