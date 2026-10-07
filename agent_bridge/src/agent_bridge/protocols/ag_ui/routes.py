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

import dataclasses
import logging
from contextlib import nullcontext, suppress

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from agent.latency import RunLatencyContext, log_run_latency, monotonic_ms
from agent.session_requests.errors import SessionRequestError
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from agent import AttachmentRef

from ...assembly import BridgeRuntime
from ...attachment_inputs import (
    AttachmentInputPlan,
    DraftAttachmentInput,
    prepare_attachment_input,
)
from ...session_bridge import PersistentBootstrap, SessionBridge
from .decoders import DecodeError, decode_run_agent_input
from .encoders import encode_stream

logger = logging.getLogger(__name__)


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 ``POST /ag-ui/run`` 挂到 :class:`FastAPI` 实例。"""
    bridge = SessionBridge(runtime)
    from agent.session_requests import RequestOptions

    from ...session_requests import SessionRequestService

    request_service = runtime.session_request_service or (
        SessionRequestService(runtime) if runtime.agent_runtime else None
    )
    router = APIRouter()

    @router.post("/ag-ui/run", response_model=None)
    async def ag_ui_run(
        payload: RunAgentInput, request: Request, enqueue: bool = False
    ) -> StreamingResponse | JSONResponse:
        received_ms = monotonic_ms()
        try:
            decoded = decode_run_agent_input(payload)
        except DecodeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        with suppress(Exception):
            log_run_latency(
                logger,
                RunLatencyContext(decoded.run_id, decoded.thread_id, received_ms),
                "route_decoded",
            )

        boot = PersistentBootstrap(
            thread_id=decoded.thread_id,
            new_user_input=decoded.new_user_input,
            default_persona=runtime.default_persona,
            default_model=(
                decoded.requested_model_config.model_id
                if decoded.requested_model_config is not None
                else runtime.default_model
            ),
            default_reasoning_preset=(
                decoded.requested_model_config.reasoning_preset
                if decoded.requested_model_config is not None
                else None
            ),
            initial_approval_mode=decoded.initial_approval_mode,
        )

        attachment_plan: AttachmentInputPlan | None = None
        resolved_attachments: list[AttachmentRef] | None = None

        def _attachments() -> list[AttachmentRef]:
            if resolved_attachments is not None:
                return resolved_attachments
            assert attachment_plan is not None
            return attachment_plan.resolve()

        accept = request.headers.get("accept") or ""
        handle = None
        if runtime.agent_runtime is not None:
            assert request_service is not None
            try:
                with (
                    runtime.attachment_store.reference_guard
                    if runtime.attachment_store
                    else nullcontext()
                ):
                    # Consume draft leases on admission, not after an arbitrarily long queue wait.
                    attachment_plan = prepare_attachment_input(
                        [
                            DraftAttachmentInput(draft_id=item.draft_id, kind=item.kind)
                            for item in decoded.draft_attachments
                        ],
                        model_id=bridge.persistent_model(
                            decoded.thread_id, default=boot.default_model
                        ),
                        store=runtime.attachment_store,
                        capability_resolver=runtime.attachment_capabilities,
                        session_files_available=runtime.session_files_available,
                    )
                    handle = request_service.submit(
                        boot=boot,
                        run_id=decoded.run_id,
                        user_event_id=decoded.user_message_id,
                        options=RequestOptions(
                            attachments=tuple(attachment_plan.resolve()),
                            model=decoded.requested_model_config if enqueue else None,
                        ),
                        references=decoded.references,
                        skills=decoded.skills,
                        allow_create=True,
                        latency_started_ms=received_ms,
                        detached=enqueue,
                    )
            except SessionRequestError as exc:
                raise HTTPException(
                    409, detail={"error": exc.kind, "message": exc.message}
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    409, detail={"error": "request_conflict", "message": str(exc)}
                ) from exc
            if enqueue:
                return JSONResponse(
                    jsonable_encoder(dataclasses.asdict(handle.snapshot())), status_code=202
                )

        else:
            if enqueue:
                raise HTTPException(503, detail="runtime unavailable")
            attachment_plan = prepare_attachment_input(
                [
                    DraftAttachmentInput(draft_id=item.draft_id, kind=item.kind)
                    for item in decoded.draft_attachments
                ],
                model_id=bridge.persistent_model(decoded.thread_id, default=boot.default_model),
                store=runtime.attachment_store,
                capability_resolver=runtime.attachment_capabilities,
                session_files_available=runtime.session_files_available,
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
                    attachments=_attachments(),
                    references=list(decoded.references),
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
