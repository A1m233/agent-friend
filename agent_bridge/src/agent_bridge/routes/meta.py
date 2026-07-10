"""非对话元数据 REST 接口（design §4.11）。

| Endpoint                          | 用途                                   | 等价 CLI 命令      |
| --------------------------------- | -------------------------------------- | ------------------ |
| ``GET /v1/sessions``              | 列出 sessions                          | ``/sessions``      |
| ``POST /v1/sessions``             | 显式创建 session（007 起；voice_bridge 用） | （内部）       |
| ``GET /v1/sessions/{id}``         | 单个 session 的 events（调试用）       | （内部）           |
| ``POST /v1/sessions/{id}/edit-resend-latest`` | 编辑并重发最后一条 user 输入 | （内部） |
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
from typing import Any, Literal
from uuid import uuid4

from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import (
    PersonaAmbiguousError,
    PersonaNotFoundError,
    SessionNotFoundError,
    SessionPersistError,
)

from ..assembly import BridgeRuntime
from ..protocols.ag_ui.encoders import encode_stream

logger = logging.getLogger(__name__)


class PersonaSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/persona`` 请求体。"""

    persona: str = Field(..., description="目标 persona name（slug）")


class ModelSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/model`` 请求体。"""

    model: str = Field(..., description="目标 model 名（LiteLLM 风格）")


class CreateSessionBody(BaseModel):
    """``POST /v1/sessions`` 请求体（007 起新增）。"""

    persona: str | None = Field(None, description="目标 persona name；不传走 bridge 默认")
    model: str | None = Field(None, description="目标 model 名；不传走 bridge 默认")
    channel: Literal["voice", "text"] = Field(
        "text", description="初始 channel；voice_bridge 拨打通话时传 voice"
    )


class ChannelSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/channel`` 请求体（007 起新增）。"""

    channel: Literal["voice", "text"] = Field(..., description="目标 channel")


class EditResendLatestBody(BaseModel):
    """``POST /v1/sessions/{id}/edit-resend-latest`` 请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(..., min_length=1, description="替换后的用户输入")
    expected_user_content: str | None = Field(
        None,
        alias="expectedUserContent",
        description="客户端看到的旧最后一条 user 内容，用于并发变化保护",
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
            return [_dataclass_to_dict(s) for s in mgr.list(archived=archived, query=q)]
        except SessionPersistError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

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
                persona_id=persona_info.id,
                channel=body.channel,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "session_id": session.session_id,
            "persona": session.current_persona,
            "model": session.current_model,
            "channel": session.current_channel,
        }

    @router.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        """单个 session 的 ``events`` 列表（调试用，纯 REST 不走协议）。"""
        try:
            session = mgr.open(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "session_id": session.session_id,
            "title": session.initial_title,
            "persona": session.current_persona,
            "model": session.current_model,
            "events": [_dataclass_to_dict(ev) for ev in session.events],
            "active_events": [_dataclass_to_dict(ev) for ev in session.active_events],
        }

    @router.patch("/sessions/{session_id}")
    def patch_session(session_id: str, body: SessionPatchBody) -> dict[str, Any]:
        """更新会话管理 metadata：展示标题 / 归档状态。"""
        if body.title is None and body.archived is None:
            raise HTTPException(status_code=400, detail="至少需要提供 title 或 archived")
        try:
            summary = None
            if body.title is not None:
                summary = mgr.rename(session_id, body.title)
            if body.archived is True:
                summary = mgr.archive(session_id)
            elif body.archived is False:
                summary = mgr.unarchive(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except SessionPersistError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        if summary is None:  # 防御：前面已检查至少一个字段。
            raise HTTPException(status_code=400, detail="没有可更新字段")
        return _dataclass_to_dict(summary)

    @router.post("/sessions/{session_id}/edit-resend-latest")
    async def edit_resend_latest(
        session_id: str,
        body: EditResendLatestBody,
        request: Request,
    ) -> StreamingResponse:
        """编辑并重发最后一条 active user 消息，返回 AG-UI SSE。"""
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        try:
            mgr.open(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        accept = request.headers.get("accept") or ""
        return StreamingResponse(
            encode_stream(
                lambda: mgr.start_conversation(mgr.open(session_id)),
                text,
                thread_id=session_id,
                run_id=uuid4().hex,
                accept=accept,
                agent_runtime=runtime.agent_runtime,
                run_conversation=lambda conv, value: conv.edit_resend_latest(
                    value,
                    expected_user_content=body.expected_user_content,
                    channel="text",
                ),
                channel="text",
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.delete("/sessions/archived")
    def delete_archived_sessions(
        q: str | None = Query(None, description="可选：只删除标题搜索命中的归档会话"),
    ) -> dict[str, Any]:
        """删除已归档会话。"""
        try:
            deleted = mgr.delete_archived(query=q)
        except SessionPersistError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"deleted": len(deleted), "session_ids": deleted}

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        """删除 session（内部清理用）。

        当前唯一调用方是 voice_bridge：如果它为一次语音通话临时创建了 session，
        但挂断时发现没有产生任何 user / assistant 消息，就删除这条空记录，避免
        历史列表被无内容通话污染。
        """
        try:
            mgr.delete(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except SessionPersistError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"session_id": session_id, "deleted": True}

    @router.post("/sessions/{session_id}/persona")
    def switch_persona(session_id: str, body: PersonaSwitchBody) -> dict[str, Any]:
        """切换 session 当前 persona；落盘 ``persona_change`` 事件。"""
        try:
            persona_info = runtime.catalog.find_by_name(body.persona)
        except (PersonaNotFoundError, PersonaAmbiguousError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            session = mgr.open(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        conv = mgr.start_conversation(session)
        conv.switch_persona(persona_info.id)

        return {
            "session_id": session.session_id,
            "persona": session.current_persona,
            "persona_id": persona_info.id,
        }

    @router.post("/sessions/{session_id}/model")
    def switch_model(session_id: str, body: ModelSwitchBody) -> dict[str, Any]:
        """切换 session 当前 model；落盘 ``model_change`` 事件。"""
        try:
            session = mgr.open(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        conv = mgr.start_conversation(session)
        conv.switch_model(body.model)

        return {
            "session_id": session.session_id,
            "model": session.current_model,
        }

    @router.post("/sessions/{session_id}/channel")
    def switch_channel(session_id: str, body: ChannelSwitchBody) -> dict[str, Any]:
        """切换 session 当前 channel（007 起新增）；落盘 ``channel_change`` 事件。

        与 persona / model 切换同模式：幂等（与当前 channel 相同时 no-op）。
        """
        try:
            session = mgr.open(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        conv = mgr.start_conversation(session)
        conv.switch_channel(body.channel)

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
