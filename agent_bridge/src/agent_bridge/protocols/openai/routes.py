"""OpenAI ChatCompletion 路由层：把 HTTP 请求接进来、装配 :class:`Conversation`、
按 ``stream: true|false`` 分支调编码器。

本期错误处理是**最小可用**版本：

- 解码失败 → HTTP 400
- 装配失败（persona / LLMAuth）→ HTTP 500（暂时直接抛，M6.3 接入完整错误模型
  转换层后再统一拟人化）

详见 docs/requirements/006-agent-bridge/design.md §4.3.2 / §4.6。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...assembly import BridgeRuntime
from ...session_bridge import SessionBridge, TransientBootstrap
from .decoders import DecodeError, decode_chat_completion_request
from .encoders import encode_nonstreaming, encode_streaming

logger = logging.getLogger(__name__)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 ``POST /v1/chat/completions`` 挂到 :class:`FastAPI` 实例。

    路由函数是普通同步函数 —— :class:`agent.Conversation` 的 ``stream`` 是
    同步生成器（基于 :class:`llm_providers.LLMClient.stream`），FastAPI 在
    StreamingResponse 里能正确驱动同步迭代器。

    Args:
        app: 已建好的 :class:`FastAPI` 实例。
        runtime: 已装配好的 :class:`BridgeRuntime`。
    """
    bridge = SessionBridge(runtime)
    router = APIRouter()

    @router.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        try:
            body = await request.json()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"非法 JSON body: {e}") from e

        try:
            decoded = decode_chat_completion_request(body)
        except DecodeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        model = decoded.model or runtime.default_model

        boot = TransientBootstrap(
            history=decoded.history,
            latest_user_input=decoded.latest_user_input,
            persona=runtime.default_persona,
            model=model,
        )
        conv = bridge.start_transient(boot)

        if decoded.stream:
            return StreamingResponse(
                encode_streaming(conv, decoded.latest_user_input, model=model),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        payload = encode_nonstreaming(conv, decoded.latest_user_input, model=model)
        return JSONResponse(content=payload)

    app.include_router(router)
