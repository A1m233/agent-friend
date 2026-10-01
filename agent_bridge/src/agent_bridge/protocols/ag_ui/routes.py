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
from collections.abc import Callable
from contextlib import suppress

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from agent.latency import RunLatencyContext, log_run_latency, monotonic_ms
from agent.runtime import ConsumerAbandonPolicy, InteractiveBusyPolicy, UserEvent
from agent.runtime.models import RunRequest
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent import AttachmentRef, Conversation, SessionNotFoundError

from ...assembly import BridgeRuntime
from ...attachment_inputs import (
    AttachmentInput,
    AttachmentInputPlan,
    DraftAttachmentInput,
    prepare_attachment_input,
    prepare_run_attachment_input,
)
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

        def _prepare(cancel_check: Callable[[], None]) -> Conversation:
            nonlocal attachment_plan, resolved_attachments
            cancel_check()
            try:
                session = runtime.persistent_session_manager.open_for_execution(
                    decoded.thread_id, cancel_check=cancel_check
                )
            except SessionNotFoundError:
                session = None
            cancel_check()
            attachment_inputs: list[AttachmentInput] = [
                DraftAttachmentInput(draft_id=item.draft_id, kind=item.kind)
                for item in decoded.draft_attachments
            ]
            prepared_model = session.current_model if session is not None else boot.default_model
            attachment_plan = prepare_run_attachment_input(
                attachment_inputs,
                model_id=prepared_model,
                store=runtime.attachment_store,
                capability_resolver=runtime.attachment_capabilities,
                session_files_available=runtime.session_files_available,
                cancel_check=cancel_check,
            )
            conv = bridge.bind_persistent(
                boot,
                post_turn_external=True,
                cancel_check=cancel_check,
                existing_session=session,
            )
            cancel_check()
            if conv.current_model_config.model_id != prepared_model:
                # A cold source may have changed before ownership was acquired.
                # Revalidate drafts against the authoritative restored model.
                attachment_plan = prepare_run_attachment_input(
                    attachment_inputs,
                    model_id=conv.current_model_config.model_id,
                    store=runtime.attachment_store,
                    capability_resolver=runtime.attachment_capabilities,
                    session_files_available=runtime.session_files_available,
                    cancel_check=cancel_check,
                )
            resolved_attachments = attachment_plan.resolve()
            cancel_check()
            return conv

        def _attachments() -> list[AttachmentRef]:
            if resolved_attachments is not None:
                return resolved_attachments
            assert attachment_plan is not None
            return attachment_plan.resolve()

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
                    reason=err.reason,
                    recovery_action=err.recovery_action,
                )

            handle = runtime.agent_runtime.submit(
                UserEvent(
                    session_id=decoded.thread_id,
                    user_input=decoded.new_user_input,
                ),
                run_id=decoded.run_id,
                latency_started_ms=received_ms,
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                conversation_preparer=_prepare,
                metadata={"protocol": "ag_ui"},
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
                control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
                input_accepted=False,
                conversation_runner=lambda conv, run_request, token: conv.stream(
                    decoded.new_user_input,
                    attachments=_attachments(),
                    references=list(decoded.references),
                    user_event_id=decoded.user_message_id,
                    channel=run_request.channel,
                    surface=run_request.surface,
                    run_id=run_request.run_id,
                    cancel_check=token.raise_if_cancelled,
                    skill_selections=decoded.skills,
                ),
                error_callback=_persist_error,
            )
        else:
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
