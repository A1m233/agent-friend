"""AG-UI 路由层：把 :class:`ag_ui.core.RunAgentInput` 接进来、装配
:class:`Conversation`、按 SSE 流式返回。

错误处理两层：

- **decode 阶段**（``thread_id`` / ``run_id`` / ``messages`` 形态校验）→
  HTTP 400，**不**走 ``RUN_ERROR``——此时 SSE 流还没启动，客户端拿到的是
  普通 HTTP 错误响应
- **装配 / stream 阶段**（session 创建 / open、LLM 调用等）→ ``RUN_STARTED``
  之后任何失败都用 ``RUN_ERROR`` 收尾（详见 design §4.6.3，由
  :func:`encode_stream` 统一兜底）

详见 docs/requirements/006-agent-bridge/design.md §4.4 / §4.6.3。
"""

from __future__ import annotations

import logging

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...assembly import BridgeRuntime
from ...session_bridge import PersistentBootstrap, SessionBridge
from .decoders import DecodeError, decode_run_agent_input
from .encoders import encode_stream

logger = logging.getLogger(__name__)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 ``POST /ag-ui/run`` 挂到 :class:`FastAPI` 实例。"""
    bridge = SessionBridge(runtime)
    router = APIRouter()

    @router.post("/ag-ui/run")
    async def ag_ui_run(payload: RunAgentInput, request: Request) -> StreamingResponse:
        try:
            decoded = decode_run_agent_input(payload)
        except DecodeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        boot = PersistentBootstrap(
            thread_id=decoded.thread_id,
            new_user_input=decoded.new_user_input,
            default_persona=runtime.default_persona,
            default_model=runtime.default_model,
        )

        accept = request.headers.get("accept") or ""

        return StreamingResponse(
            encode_stream(
                lambda: bridge.bind_persistent(boot),
                decoded.new_user_input,
                thread_id=decoded.thread_id,
                run_id=decoded.run_id,
                accept=accept,
                agent_runtime=runtime.agent_runtime,
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(router)
