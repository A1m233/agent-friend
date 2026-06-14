"""LLM 入站代理：火山 RTC ``CustomLLM`` 调过来 → 注入 session header → 转发 agent_bridge。

完整透明代理：除了注入 ``X-Agent-Friend-Session-Id``，body / headers / SSE 内容
**全部原样转发**——不解析、不修改、不引入额外延迟。这是 RTC 全双工/打断体验的硬约束。

详见 docs/requirements/007-voice-call/design.md §4.4。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..assembly import VoiceBridgeRuntime
from ..errors import UnknownCallError

logger = logging.getLogger(__name__)


_HOP_BY_HOP_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}
)


def register_llm_proxy_routes(app: FastAPI, runtime: VoiceBridgeRuntime) -> None:
    """把 LLM 入站代理路由挂到 FastAPI 实例。"""
    router = APIRouter(prefix="/voice/llm", tags=["voice-llm-proxy"])

    @router.post("/{call_id}/v1/chat/completions")
    async def proxy_chat_completions(call_id: str, request: Request) -> StreamingResponse:
        """OpenAI ChatCompletion 协议入口（火山 RTC ``LLMConfig.Url`` 指向这里）。"""
        binding = runtime.call_registry.lookup(call_id)
        if binding is None:
            err = UnknownCallError()
            raise HTTPException(
                status_code=err.info.http_status,
                detail={"error": err.info.error_code, "message": err.info.user_message},
            )

        upstream_url = runtime.settings.agent_bridge_url.rstrip("/") + "/v1/chat/completions"
        body = await request.body()
        upstream_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        upstream_headers["X-Agent-Friend-Session-Id"] = binding.session_id

        async def stream() -> AsyncIterator[bytes]:
            # 嵌套 with 是有意为之：合并到单个 with 在某些 mypy 版本下会把
            # `client.stream(...)` 的返回类型擦成 Any。SIM117 在这里安全可忽略。
            async with httpx.AsyncClient(timeout=None) as client:  # noqa: SIM117
                async with client.stream(
                    "POST",
                    upstream_url,
                    content=body,
                    headers=upstream_headers,
                ) as resp:
                    if resp.status_code >= 400:
                        error_body = await resp.aread()
                        logger.warning(
                            "agent_bridge 返回 %d: %s", resp.status_code, error_body[:200]
                        )
                        yield error_body
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(stream(), media_type="text/event-stream")

    app.include_router(router)
