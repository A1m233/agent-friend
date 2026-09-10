"""非对话元数据 REST 接口（design §4.11）。

| Endpoint                          | 用途                                   | 等价 CLI 命令      |
| --------------------------------- | -------------------------------------- | ------------------ |
| ``GET /v1/sessions``              | 列出 sessions                          | ``/sessions``      |
| ``GET /v1/sessions/search``       | 搜索全部用户可见会话内容               | （内部）           |
| ``POST /v1/sessions``             | 显式创建 session（007 起；voice_bridge 用） | （内部）       |
| ``GET /v1/sessions/{id}``         | 单个 session 的 events（调试用）       | （内部）           |
| ``POST /v1/sessions/{id}/edit-resend-latest`` | 编辑并重发最后一条 user 输入 | （内部） |
| ``POST /v1/sessions/{id}/continue-tool-result`` | 从已完成工具结果继续整理 | （内部） |
| ``DELETE /v1/sessions/{id}``      | 删除 session（内部清理用）            | （内部）           |
| ``POST /v1/sessions/{id}/persona`` | 切换 persona                          | ``/persona <name>``|
| ``POST /v1/sessions/{id}/model``  | 切换 model                             | ``/model <name>``  |
| ``POST /v1/sessions/{id}/channel`` | 切换 channel（007 起）                | （内部）           |
| ``GET /v1/personas``              | 列出可用 persona                       | ``/personas``      |

这些接口**不**遵循 OpenAI 或 AG-UI 协议——是 bridge 自家的 REST，客户端是
``agent-cli --bridge`` 或调试工具，不是协议兼容客户端。schema 稳定性见
design §6.2（弱稳定，本期落地后默认不变）。
"""

from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Annotated, Any, Literal
from uuid import uuid4

from ag_ui.encoder import EventEncoder
from agent.runtime import ConsumerAbandonPolicy, InteractiveBusyPolicy, UserEvent
from agent.runtime.cancellation import CancellationToken
from agent.runtime.models import RunRequest
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import (
    AttachmentRef,
    Conversation,
    ConversationEvent,
    PersonaAmbiguousError,
    PersonaNotFoundError,
    QuotedTextReference,
    SessionNotFoundError,
    SessionPersistError,
    parse_quoted_text_references,
    quoted_text_references_fingerprint,
)

from ..assembly import BridgeRuntime
from ..attachment_inputs import (
    AttachmentInput,
    DraftAttachmentInput,
    ExistingAttachmentInput,
    prepare_attachment_input,
)
from ..attachment_reconciliation import reconcile_attachments
from ..errors import build_openai_error_envelope, map_exception
from ..protocols.ag_ui.encoders import encode_stream
from .runs import DESKTOP_TEXT_CONTROL_SCOPE

logger = logging.getLogger(__name__)


class PersonaSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/persona`` 请求体。"""

    persona: str = Field(..., description="目标 persona name（slug）")


class ModelSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/model`` 请求体。"""

    model: str = Field(..., description="目标 model 名（LiteLLM 风格）")


class RuntimeConfigPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str | None = Field(None, description="目标 model id")
    reasoning_preset: str | None = Field(None, alias="reasoningPreset")


class CreateSessionBody(BaseModel):
    """``POST /v1/sessions`` 请求体（007 起新增）。"""

    persona: str | None = Field(None, description="目标 persona name；不传走 bridge 默认")
    model: str | None = Field(None, description="目标 model 名；不传走 bridge 默认")
    reasoning_preset: str | None = Field(None, alias="reasoningPreset")
    channel: Literal["voice", "text"] = Field(
        "text", description="初始 channel；voice_bridge 拨打通话时传 voice"
    )


class ChannelSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/channel`` 请求体（007 起新增）。"""

    channel: Literal["voice", "text"] = Field(..., description="目标 channel")


class ExistingEditAttachmentBody(BaseModel):
    source: Literal["existing"]
    asset_id: str = Field(..., alias="assetId")


class DraftEditAttachmentBody(BaseModel):
    source: Literal["draft"]
    draft_id: str = Field(..., alias="draftId")
    kind: Literal["image", "document"]


EditAttachmentBody = Annotated[
    ExistingEditAttachmentBody | DraftEditAttachmentBody,
    Field(discriminator="source"),
]


class EditResendLatestBody(BaseModel):
    """``POST /v1/sessions/{id}/edit-resend-latest`` 请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(..., description="替换后的用户输入；有附件时允许为空")
    run_id: str | None = Field(
        None,
        alias="runId",
        description="客户端预生成的稳定 run id；旧客户端不传时由 Bridge 生成",
    )
    expected_user_content: str | None = Field(
        None,
        alias="expectedUserContent",
        description="客户端看到的旧最后一条 user 内容，用于并发变化保护",
    )
    expected_user_message_id: str | None = Field(
        None,
        alias="expectedUserMessageId",
        description="客户端看到的旧最后一条 user event UUID，用于并发变化保护",
    )
    attachments: list[EditAttachmentBody] | None = Field(
        None,
        description="最终附件替换集；缺省时保留全部原附件，空数组表示删除全部",
    )
    references: list[dict[str, Any]] | None = Field(
        None,
        description="最终引用替换集；缺省时保留原引用，空数组表示删除全部",
    )
    expected_reference_fingerprint: str | None = Field(
        None,
        alias="expectedReferenceFingerprint",
        description="客户端看到的旧引用集合指纹，用于并发变化保护",
    )


class ContinueToolResultBody(BaseModel):
    """``POST /v1/sessions/{id}/continue-tool-result`` 请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    run_id: str | None = Field(
        None,
        alias="runId",
        description="客户端预生成的稳定 run id；旧客户端不传时由 Bridge 生成",
    )


class SessionPatchBody(BaseModel):
    """``PATCH /v1/sessions/{id}`` 请求体。"""

    title: str | None = Field(None, description="新的会话展示标题")
    archived: bool | None = Field(None, description="归档状态；true=归档，false=取消归档")


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 meta 路由挂到 :class:`FastAPI` 实例。"""
    router = APIRouter(prefix="/v1", tags=["meta"])
    mgr = runtime.persistent_session_manager

    @router.get("/sessions")
    def list_sessions(
        archived: bool = Query(False, description="是否返回已归档会话"),
        q: str | None = Query(None, description="按展示标题搜索"),
    ) -> list[dict[str, Any]]:
        """返回 ``list[SessionSummary]``（按最近活跃时间倒序）。"""
        try:
            return [_session_summary_to_dict(s) for s in mgr.list(archived=archived, query=q)]
        except SessionPersistError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get("/execution-environments/diagnostics")
    def execution_environment_diagnostics() -> dict[str, object]:
        """返回不含 session id、路径、包名、脚本和输出的受管环境摘要。"""
        manager = runtime.execution_environment_manager
        if manager is None:
            return {"schemaVersion": 1, "status": "unavailable", "providers": []}
        return manager.collect_diagnostics()

    @router.post("/sessions")
    def create_session(body: CreateSessionBody) -> dict[str, Any]:
        """显式创建 session（007 起新增；voice_bridge 拨打通话时调用）。

        - body 不传 ``persona`` / ``model`` 时走 bridge 默认
        - ``channel`` 默认 ``"text"``，voice_bridge 创建通话时传 ``"voice"``
        - 创建即落盘 ``session_meta`` 事件（含 ``initial_channel`` 字段）
        """
        persona_name = body.persona or runtime.default_persona
        try:
            persona_info = runtime.catalog.find_by_name(persona_name)
        except (PersonaNotFoundError, PersonaAmbiguousError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        model_name = body.model or runtime.default_model
        try:
            session = mgr.create(
                persona=persona_info.name,
                model=model_name,
                reasoning_preset=body.reasoning_preset,
                persona_id=persona_info.id,
                channel=body.channel,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if runtime.execution_environment_manager is not None:
            runtime.execution_environment_manager.schedule_session(session.session_id)
        return {
            "session_id": session.session_id,
            "persona": session.current_persona,
            "model": session.current_model,
            "reasoningPreset": session.current_model_config.reasoning_preset,
            "channel": session.current_channel,
        }

    @router.get("/sessions/search")
    def search_sessions(
        q: str = Query(..., min_length=1, max_length=200, description="连续文本关键词"),
        message_limit: int = Query(50, ge=1, le=50),
        title_limit: int = Query(10, ge=1, le=10),
    ) -> dict[str, Any]:
        """搜索普通和归档会话的标题与用户可见消息。"""
        service = runtime.session_search_service
        if service is None:
            raise HTTPException(status_code=503, detail="全局会话搜索服务未装配")
        try:
            result = service.search(
                q,
                message_limit=message_limit,
                title_limit=title_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SessionPersistError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _dataclass_to_dict(result)

    @router.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        view: Literal["full", "active"] = Query(
            "full",
            description="full 保留原始事件；active 只返回当前有效事件",
        ),
    ) -> dict[str, Any]:
        """返回单个 session 的事件列表（纯 REST 不走协议）。

        ``full`` 是向后兼容的默认响应；桌面聊天只需要 ``active`` 投影视图，
        可避免把原始事件与有效事件同时传到前端。
        """
        try:
            revision_before = mgr.current_revision(session_id)
            session = mgr.query(session_id) if view == "active" else mgr.open(session_id)
            revision = mgr.current_revision(session_id)
            if revision != revision_before:
                session = mgr.query(session_id) if view == "active" else mgr.open(session_id)
                revision = mgr.current_revision(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        active_events = [_public_event_dict(ev) for ev in session.active_events]
        detail = {
            "session_id": session.session_id,
            "title": session.initial_title,
            "persona": session.current_persona,
            "model": session.current_model,
            "reasoningPreset": session.current_model_config.reasoning_preset,
            "revision": revision,
            **mgr.approval_mode_state(session).to_dict(),
        }
        if view == "active":
            return {**detail, "active_events": active_events}
        return {
            **detail,
            "events": [_public_event_dict(ev) for ev in session.events],
            "active_events": active_events,
        }

    @router.patch("/sessions/{session_id}")
    def patch_session(session_id: str, body: SessionPatchBody) -> Any:
        """更新会话管理 metadata：展示标题 / 归档状态。"""
        if body.title is None and body.archived is None:
            raise HTTPException(status_code=400, detail="至少需要提供 title 或 archived")
        try:
            with ExitStack() as stack:
                if body.archived is not None:
                    if runtime.agent_runtime is not None:
                        stack.enter_context(
                            runtime.agent_runtime.try_reserve_session_mutation(session_id)
                        )
                    stack.enter_context(runtime.turn_coordinator.try_hold(session_id))
                summary = None
                if body.title is not None:
                    summary = mgr.rename(session_id, body.title)
                if body.archived is True:
                    summary = mgr.archive(session_id)
                elif body.archived is False:
                    summary = mgr.unarchive(session_id)
        except Exception as exc:
            if isinstance(exc, SessionNotFoundError):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )
        if summary is None:  # 防御：前面已检查至少一个字段。
            raise HTTPException(status_code=400, detail="没有可更新字段")
        return _session_summary_to_dict(summary)

    @router.post("/sessions/{session_id}/edit-resend-latest")
    async def edit_resend_latest(
        session_id: str,
        body: EditResendLatestBody,
        request: Request,
    ) -> StreamingResponse:
        """编辑并重发最后一条 active user 消息，返回 AG-UI SSE。"""
        text = body.text.strip()
        accept = request.headers.get("accept") or ""
        run_id = body.run_id or uuid4().hex

        try:
            preflight_session = mgr.open(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        target = next(
            (
                event
                for event in reversed(preflight_session.active_events)
                if event.type == "user_message"
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=400, detail="当前会话没有可编辑重发的用户消息")
        target_content = target.payload.get("content", "")
        if not isinstance(target_content, str):
            target_content = ""
        if (
            body.expected_user_message_id is not None
            and body.expected_user_message_id != target.uuid
        ) or (
            body.expected_user_content is not None and body.expected_user_content != target_content
        ):
            raise HTTPException(
                status_code=409,
                detail="最后一条用户消息已变化，请刷新后再编辑重发",
            )

        try:
            original_references = parse_quoted_text_references(target.payload.get("references", []))
            replacement_references: list[QuotedTextReference] = (
                original_references
                if body.references is None
                else parse_quoted_text_references(body.references)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"引用数据无效: {exc}") from exc
        if (
            body.expected_reference_fingerprint is not None
            and body.expected_reference_fingerprint
            != quoted_text_references_fingerprint(original_references)
        ):
            raise HTTPException(
                status_code=409,
                detail="最后一条用户消息的引用已变化，请刷新后再编辑重发",
            )

        try:
            original_attachments = [
                AttachmentRef.from_dict(item)
                for item in target.payload.get("attachments", [])
                if isinstance(item, dict)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="历史附件数据无法读取") from exc

        if body.attachments is None:
            attachment_inputs: list[AttachmentInput] = [
                ExistingAttachmentInput(ref) for ref in original_attachments
            ]
        else:
            original_by_asset: dict[str, list[AttachmentRef]] = defaultdict(list)
            for ref in original_attachments:
                original_by_asset[ref.asset_id].append(ref)
            attachment_inputs = []
            for item in body.attachments:
                if isinstance(item, ExistingEditAttachmentBody):
                    candidates = original_by_asset[item.asset_id]
                    if not candidates:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": "attachment_not_on_edit_target",
                                "message": "只能保留当前编辑消息原有的附件。",
                            },
                        )
                    attachment_inputs.append(ExistingAttachmentInput(candidates.pop(0)))
                else:
                    attachment_inputs.append(
                        DraftAttachmentInput(draft_id=item.draft_id, kind=item.kind)
                    )

        attachment_plan = prepare_attachment_input(
            attachment_inputs,
            model_id=preflight_session.current_model,
            store=runtime.attachment_store,
            capability_resolver=runtime.attachment_capabilities,
        )
        if not text and not attachment_plan.validated_refs and not replacement_references:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "empty_edit_resend",
                    "message": "编辑后的文字、附件和引用不能同时为空。",
                },
            )

        def _conversation_for_edit() -> Conversation:
            session = mgr.open(session_id)
            return mgr.start_conversation(
                session,
                post_turn_external=runtime.agent_runtime is not None,
            )

        handle = None
        if runtime.agent_runtime is not None:

            def _edit_runner(
                conv: Conversation,
                run_request: RunRequest,
                token: CancellationToken,
            ) -> Iterator[ConversationEvent]:
                return conv.edit_resend_latest(
                    text,
                    attachments=attachment_plan.resolve(),
                    references=replacement_references,
                    expected_user_uuid=target.uuid,
                    expected_user_content=target_content,
                    expected_reference_fingerprint=quoted_text_references_fingerprint(
                        original_references
                    ),
                    channel="text",
                    surface=run_request.surface,
                    run_id=run_request.run_id,
                    cancel_check=token.raise_if_cancelled,
                )

            handle = runtime.agent_runtime.submit(
                UserEvent(session_id=session_id, user_input=text),
                run_id=run_id,
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                conversation_factory=_conversation_for_edit,
                conversation_runner=_edit_runner,
                metadata={"protocol": "ag_ui", "operation": "edit_resend_latest"},
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
                control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
            )

        return StreamingResponse(
            encode_stream(
                _conversation_for_edit,
                text,
                thread_id=session_id,
                run_id=run_id,
                accept=accept,
                agent_runtime=runtime.agent_runtime,
                run_conversation=lambda conv, value: conv.edit_resend_latest(
                    value,
                    attachments=attachment_plan.resolve(),
                    references=replacement_references,
                    expected_user_uuid=target.uuid,
                    expected_user_content=target_content,
                    expected_reference_fingerprint=quoted_text_references_fingerprint(
                        original_references
                    ),
                    channel="text",
                    surface=DESKTOP_CHAT_SURFACE,
                ),
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                turn_coordinator=runtime.turn_coordinator,
                run_handle=handle,
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/sessions/{session_id}/continue-tool-result")
    async def continue_tool_result(
        session_id: str,
        request: Request,
        body: ContinueToolResultBody | None = None,
    ) -> StreamingResponse:
        """从最新可恢复失败轮的已完成工具结果继续生成，禁止再次调用工具。"""
        accept = request.headers.get("accept") or ""
        run_id = body.run_id if body and body.run_id else uuid4().hex

        def _conversation_for_continue() -> Conversation:
            session = mgr.open(session_id)
            return mgr.start_conversation(
                session,
                post_turn_external=runtime.agent_runtime is not None,
            )

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

        handle = None
        if runtime.agent_runtime is not None:
            handle = runtime.agent_runtime.submit(
                UserEvent(session_id=session_id, user_input=""),
                run_id=run_id,
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                conversation_factory=_conversation_for_continue,
                metadata={"protocol": "ag_ui", "operation": "continue_tool_result"},
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                interactive_busy_policy=InteractiveBusyPolicy.REJECT_IF_INTERACTIVE_BUSY,
                control_scope=DESKTOP_TEXT_CONTROL_SCOPE,
                conversation_runner=lambda conv, run_request, token: conv.continue_after_tool_error(
                    channel="text",
                    surface=run_request.surface,
                    run_id=run_request.run_id,
                    cancel_check=token.raise_if_cancelled,
                ),
                error_callback=_persist_error,
            )

        return StreamingResponse(
            encode_stream(
                _conversation_for_continue,
                "",
                thread_id=session_id,
                run_id=run_id,
                accept=accept,
                agent_runtime=runtime.agent_runtime,
                run_conversation=lambda conv, _value: conv.continue_after_tool_error(
                    channel="text",
                    surface=DESKTOP_CHAT_SURFACE,
                    run_id=run_id,
                ),
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                turn_coordinator=runtime.turn_coordinator,
                run_handle=handle,
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.delete("/sessions/archived")
    def delete_archived_sessions(
        q: str | None = Query(None, description="可选：只删除标题搜索命中的归档会话"),
    ) -> Any:
        """删除已归档会话。"""
        try:
            targets = mgr.list(archived=True, query=q)
            with ExitStack() as stack:
                for summary in sorted(targets, key=lambda item: item.session_id):
                    if runtime.agent_runtime is not None:
                        stack.enter_context(
                            runtime.agent_runtime.try_reserve_session_mutation(summary.session_id)
                        )
                    stack.enter_context(runtime.turn_coordinator.try_hold(summary.session_id))
                deleted = mgr.delete_archived(query=q)
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )
        reconcile_attachments(runtime)
        if runtime.execution_environment_manager is not None:
            for session_id in deleted:
                try:
                    runtime.execution_environment_manager.delete_session(session_id)
                except Exception:
                    logger.warning(
                        "execution environment cleanup failed for deleted archived session",
                        exc_info=True,
                    )
        return {"deleted": len(deleted), "session_ids": deleted}

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> Any:
        """删除 session（内部清理用）。

        当前唯一调用方是 voice_bridge：如果它为一次语音通话临时创建了 session，
        但挂断时发现没有产生任何 user / assistant 消息，就删除这条空记录，避免
        历史列表被无内容通话污染。
        """
        try:
            with ExitStack() as stack:
                if runtime.agent_runtime is not None:
                    stack.enter_context(
                        runtime.agent_runtime.try_reserve_session_mutation(session_id)
                    )
                stack.enter_context(runtime.turn_coordinator.try_hold(session_id))
                mgr.delete(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )
        reconcile_attachments(runtime)
        if runtime.execution_environment_manager is not None:
            try:
                runtime.execution_environment_manager.delete_session(session_id)
            except Exception:
                logger.warning(
                    "execution environment cleanup failed for deleted session",
                    exc_info=True,
                )
        return {"session_id": session_id, "deleted": True}

    @router.post("/sessions/{session_id}/persona")
    def switch_persona(session_id: str, body: PersonaSwitchBody) -> Any:
        """切换 session 当前 persona；落盘 ``persona_change`` 事件。"""
        try:
            persona_info = runtime.catalog.find_by_name(body.persona)
        except (PersonaNotFoundError, PersonaAmbiguousError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            with ExitStack() as stack:
                if runtime.agent_runtime is not None:
                    stack.enter_context(
                        runtime.agent_runtime.try_reserve_session_mutation(session_id)
                    )
                stack.enter_context(runtime.turn_coordinator.try_hold(session_id))
                session = mgr.open(session_id)
                conv = mgr.start_conversation(session)
                conv.switch_persona(persona_info.id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )

        return {
            "session_id": session.session_id,
            "persona": session.current_persona,
            "persona_id": persona_info.id,
        }

    @router.post("/sessions/{session_id}/model")
    def switch_model(session_id: str, body: ModelSwitchBody) -> Any:
        """切换 session 当前 model；落盘 ``model_change`` 事件。"""
        try:
            from ..session_models import SessionModelService

            service = runtime.session_model_service or SessionModelService(
                mgr,
                runtime.turn_coordinator,
                runtime.agent_runtime,
            )
            model = service.switch(session_id, body.model)
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )

        return {
            "session_id": session_id,
            "model": model,
        }

    @router.patch("/sessions/{session_id}/runtime-config")
    def update_runtime_config(session_id: str, body: RuntimeConfigPatchBody) -> Any:
        try:
            from ..session_models import SessionModelService

            service = runtime.session_model_service or SessionModelService(
                mgr,
                runtime.turn_coordinator,
                runtime.agent_runtime,
            )
            config = service.update(
                session_id,
                model=body.model,
                reasoning_preset=body.reasoning_preset,
            )
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )

        return {
            "sessionId": session_id,
            "runtimeConfig": {
                "model": config.model_id,
                "reasoningPreset": config.reasoning_preset,
            },
        }

    @router.post("/sessions/{session_id}/channel")
    def switch_channel(session_id: str, body: ChannelSwitchBody) -> Any:
        """切换 session 当前 channel（007 起新增）；落盘 ``channel_change`` 事件。

        与 persona / model 切换同模式：幂等（与当前 channel 相同时 no-op）。
        """
        try:
            with ExitStack() as stack:
                if runtime.agent_runtime is not None:
                    stack.enter_context(
                        runtime.agent_runtime.try_reserve_session_mutation(session_id)
                    )
                stack.enter_context(runtime.turn_coordinator.try_hold(session_id))
                session = mgr.open(session_id)
                conv = mgr.start_conversation(session)
                conv.switch_channel(body.channel)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as exc:
            error = map_exception(exc)
            return JSONResponse(
                status_code=error.http_status,
                content=build_openai_error_envelope(error),
            )

        return {
            "session_id": session.session_id,
            "channel": session.current_channel,
        }

    @router.get("/personas")
    def list_personas() -> list[dict[str, Any]]:
        """返回 ``list[PersonaInfo]``。"""
        return [_dataclass_to_dict(p) for p in runtime.catalog.list()]

    app.include_router(router)


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """把 frozen dataclass（``PersonaInfo`` / ``SessionSummary`` / ``Event``）
    转成 JSON 友好的 dict。

    :func:`dataclasses.asdict` 已经能递归处理嵌套 dataclass / dict / list；
    剩下的非原生类型（如 ``datetime``）交给 FastAPI 默认 JSON encoder
    （pydantic v2 内部走 ``__iso_format__``）。
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return obj
    return {"value": obj}


def _public_event_dict(obj: Any) -> dict[str, Any]:
    """Serialize an event without exposing provider reasoning continuation payloads."""
    event = _dataclass_to_dict(obj)
    if event.get("type") != "provider_reasoning_state":
        return event

    payload = event.get("payload")
    if not isinstance(payload, dict):
        event["payload"] = {"redacted": True}
        return event

    public_payload = {
        key: payload[key]
        for key in ("provider", "model", "format", "replayScope")
        if key in payload
    }
    data = payload.get("data")
    if isinstance(data, str):
        public_payload["dataCharCount"] = len(data)
    elif isinstance(data, list):
        public_payload["detailCount"] = len(data)
    for key in ("dataCharCount", "detailCount"):
        if key in payload and key not in public_payload:
            public_payload[key] = payload[key]
    public_payload["redacted"] = True
    event["payload"] = public_payload
    return event


def _session_summary_to_dict(obj: Any) -> dict[str, Any]:
    summary = _dataclass_to_dict(obj)
    summary["reasoningPreset"] = summary.pop("reasoning_preset", None)
    return summary
