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
from agent.runtime import ConsumerAbandonPolicy, InteractiveBusyPolicy, UserEvent
from agent.runtime.models import RunRequest
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent import Conversation

from ...assembly import BridgeRuntime
from ...attachment_inputs import DraftAttachmentInput, prepare_attachment_input
from ...errors import map_exception
from ...routes.runs import DESKTOP_TEXT_CONTROL_SCOPE
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
            default_model=decoded.requested_model or runtime.default_model,
        )

        effective_model = boot.default_model
        if bridge.session_exists(decoded.thread_id):
            effective_model = runtime.persistent_session_manager.open(
                decoded.thread_id
            ).current_model
        attachment_plan = prepare_attachment_input(
            [
                DraftAttachmentInput(draft_id=item.draft_id, kind=item.kind)
                for item in decoded.draft_attachments
            ],
            model_id=effective_model,
            store=runtime.attachment_store,
        )

        accept = request.headers.get("accept") or ""
        handle = None
        if runtime.agent_runtime is not None:

            def _persist_error(
                conv: Conversation,
                exc: BaseException,
                run_request: RunRequest,
            ) -> None:
                err = map_exception(exc)
                conv.append_run_error_event(
                    code=err.code,
                    message=err.message,
                    recoverable=err.recoverable,
                    source="ag_ui",
                    run_id=run_request.run_id,
                    sanitize_failed_model_call=err.sanitize_failed_model_call,
                )

            handle = runtime.agent_runtime.submit(
                UserEvent(
                    session_id=decoded.thread_id,
                    user_input=decoded.new_user_input,
                ),
                run_id=decoded.run_id,
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                conversation_factory=lambda: bridge.bind_persistent(
                    boot,
                    post_turn_external=True,
                ),
                metadata={"protocol": "ag_ui"},
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
                control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
                conversation_runner=lambda conv, run_request, token: conv.stream(
                    decoded.new_user_input,
                    attachments=attachment_plan.resolve(),
                    user_event_id=decoded.user_message_id,
                    channel=run_request.channel,
                    surface=run_request.surface,
                    run_id=run_request.run_id,
                    cancel_check=token.raise_if_cancelled,
                    skill_selections=decoded.skills,
                ),
                error_callback=_persist_error,
            )

        return StreamingResponse(
            encode_stream(
                lambda: bridge.bind_persistent(boot),
                decoded.new_user_input,
                thread_id=decoded.thread_id,
                run_id=decoded.run_id,
                accept=accept,
                agent_runtime=runtime.agent_runtime,
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                turn_coordinator=runtime.turn_coordinator,
                run_handle=handle,
                run_conversation=lambda conv, text: conv.stream(
                    text,
                    attachments=attachment_plan.resolve(),
                    user_event_id=decoded.user_message_id,
                    channel="text",
                    surface=DESKTOP_CHAT_SURFACE,
                    run_id=decoded.run_id,
                    skill_selections=decoded.skills,
                ),
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(router)
