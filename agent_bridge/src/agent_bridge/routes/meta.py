"""非对话元数据 REST 接口（design §4.11）。

| Endpoint                          | 用途                                  | 等价 CLI 命令      |
| --------------------------------- | ------------------------------------- | ------------------ |
| ``GET /v1/sessions``              | 列出 sessions                          | ``/sessions``      |
| ``GET /v1/sessions/{id}``         | 单个 session 的 events（调试用）       | （内部）           |
| ``POST /v1/sessions/{id}/persona`` | 切换 persona                          | ``/persona <name>``|
| ``POST /v1/sessions/{id}/model``  | 切换 model                            | ``/model <name>``  |
| ``GET /v1/personas``              | 列出可用 persona                       | ``/personas``      |

这些接口**不**遵循 OpenAI 或 AG-UI 协议——是 bridge 自家的 REST，客户端是
``agent-cli --bridge`` 或调试工具，不是协议兼容客户端。schema 稳定性见
design §6.2（弱稳定，本期落地后默认不变）。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import PersonaAmbiguousError, PersonaNotFoundError, SessionNotFoundError

from ..assembly import BridgeRuntime

logger = logging.getLogger(__name__)


class PersonaSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/persona`` 请求体。"""

    persona: str = Field(..., description="目标 persona name（slug）")


class ModelSwitchBody(BaseModel):
    """``POST /v1/sessions/{id}/model`` 请求体。"""

    model: str = Field(..., description="目标 model 名（LiteLLM 风格）")


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 meta 路由挂到 :class:`FastAPI` 实例。"""
    router = APIRouter(prefix="/v1", tags=["meta"])
    mgr = runtime.persistent_session_manager

    @router.get("/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """返回 ``list[SessionSummary]``（按 :meth:`SessionStore.list` 自然顺序）。"""
        return [_dataclass_to_dict(s) for s in runtime.persistent_store.list()]

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
        }

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
