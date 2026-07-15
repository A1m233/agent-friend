"""050：产品级单一人格状态、自然语言编辑与应用接口。"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    EffectivePersonaStore,
    PersonaContentInvalidError,
    PersonaEditorWorkspace,
    PersonaPersistError,
    PersonaRevisionConflictError,
    PersonaUndoUnavailableError,
    Session,
    SessionManager,
    SessionNotFoundError,
    SessionPersistError,
    extract_persona_display_name,
)

from ..assembly import BridgeRuntime, make_persona_editor_session_manager
from ..protocols.ag_ui.decoders import DecodeError, decode_run_agent_input
from ..protocols.ag_ui.encoders import encode_stream

PERSONA_EDITOR_THREAD_ID = "00000000-0000-4000-8000-000000000050"


class _RequestModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PersonaApplyRequest(_RequestModel):
    content: str = Field(max_length=32_000)
    expected_revision: str = Field(alias="expectedRevision")
    source: str = "guided"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _snapshot_payload(snapshot) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "content": snapshot.content,
        "displayName": snapshot.display_name,
        "source": snapshot.source,
        "sourceStatus": snapshot.source_status,
        "revision": snapshot.revision,
        "editableFileExists": snapshot.editable_file_exists,
        "canUndoApply": snapshot.can_undo_apply,
        "diagnostic": asdict(snapshot.diagnostic) if snapshot.diagnostic else None,
    }


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/persona", tags=["persona"])
    ag_ui_router = APIRouter(tags=["persona"])

    editor_manager = runtime.persona_editor_session_manager
    editor_workspace = runtime.persona_editor_workspace
    if editor_workspace is None and runtime.persona_store is not None:
        editor_workspace = PersonaEditorWorkspace(
            runtime.persona_store,
            runtime.settings.personas_dir / ".internal" / "default" / "editor-base-revision.txt",
        )
    if editor_manager is None and runtime.persona_store is not None and runtime.llm_client_factory:
        assert editor_workspace is not None
        editor_manager = make_persona_editor_session_manager(
            store=runtime.persona_store,
            sessions_dir=(
                runtime.settings.personas_dir / ".internal" / "default" / "editor-sessions"
            ),
            llm_client_factory=runtime.llm_client_factory,
            workspace=editor_workspace,
        )

    def store() -> EffectivePersonaStore:
        if runtime.persona_store is None:
            raise RuntimeError("persona store is not configured")
        return runtime.persona_store

    def editor() -> SessionManager:
        if editor_manager is None:
            raise RuntimeError("persona editor is not configured")
        return editor_manager

    def workspace() -> PersonaEditorWorkspace:
        if editor_workspace is None:
            raise RuntimeError("persona editor workspace is not configured")
        return editor_workspace

    def open_editor_session() -> Session:
        try:
            session = editor().open(PERSONA_EDITOR_THREAD_ID)
        except SessionNotFoundError:
            workspace().start()
            try:
                return editor().create(
                    persona=runtime.default_persona,
                    model=runtime.default_model,
                    title="人格编辑",
                    persona_id=BUILTIN_DEFAULT_PERSONA_ID,
                    session_id=PERSONA_EDITOR_THREAD_ID,
                )
            except Exception:
                with suppress(PersonaPersistError):
                    workspace().clear()
                raise
        if workspace().exists():
            return session
        # 旧开发版本可能留下没有基准 revision 的编辑会话；无法证明其基线时
        # fail closed，丢弃未应用内容并从当前正式人格重新开始。
        editor().delete(PERSONA_EDITOR_THREAD_ID)
        workspace().start()
        try:
            return editor().create(
                persona=runtime.default_persona,
                model=runtime.default_model,
                title="人格编辑",
                persona_id=BUILTIN_DEFAULT_PERSONA_ID,
                session_id=PERSONA_EDITOR_THREAD_ID,
            )
        except Exception:
            with suppress(PersonaPersistError):
                workspace().clear()
            raise

    def clear_editor_session(*, strict: bool = False) -> None:
        cleanup_error: Exception | None = None
        try:
            editor().delete(PERSONA_EDITOR_THREAD_ID)
        except SessionNotFoundError:
            pass
        except (SessionPersistError, RuntimeError) as exc:
            cleanup_error = exc
        try:
            workspace().clear()
        except (PersonaPersistError, RuntimeError) as exc:
            cleanup_error = cleanup_error or exc
        if strict and cleanup_error is not None:
            raise PersonaPersistError("清理人格编辑工作区失败，请稍后重试") from cleanup_error

    def editor_snapshot_payload() -> dict[str, object]:
        try:
            session = editor().open(PERSONA_EDITOR_THREAD_ID)
        except (SessionNotFoundError, RuntimeError):
            return {"threadId": PERSONA_EDITOR_THREAD_ID, "events": [], "candidate": None}
        if not workspace().exists():
            clear_editor_session()
            return {"threadId": PERSONA_EDITOR_THREAD_ID, "events": [], "candidate": None}
        candidate = None
        for event in session.active_events:
            if (
                event.type != "tool_call_result"
                or event.payload.get("tool_name") != "propose_persona"
            ):
                continue
            try:
                parsed = json.loads(str(event.payload.get("content", "")))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("kind") == "persona_candidate":
                content = parsed.get("content")
                if isinstance(content, str):
                    parsed["displayName"] = extract_persona_display_name(content)
                candidate = parsed
        return {
            "threadId": PERSONA_EDITOR_THREAD_ID,
            "events": [asdict(event) for event in session.active_events],
            "candidate": candidate,
        }

    @router.get("")
    def get_persona():  # type: ignore[no-untyped-def]
        try:
            return _snapshot_payload(store().load())
        except PersonaPersistError:
            return _error(
                500,
                "persona_read_failed",
                "读取人格文件失败，请检查文件权限后重试",
            )

    @router.get("/editor")
    def get_editor():  # type: ignore[no-untyped-def]
        return editor_snapshot_payload()

    @router.delete("/editor")
    def discard_editor():  # type: ignore[no-untyped-def]
        try:
            clear_editor_session(strict=True)
            return {"threadId": PERSONA_EDITOR_THREAD_ID, "events": [], "candidate": None}
        except PersonaPersistError as exc:
            return _error(500, "persona_editor_discard_failed", str(exc))

    @ag_ui_router.post("/ag-ui/persona-editor/run")
    async def run_editor(payload: RunAgentInput, request: Request):  # type: ignore[no-untyped-def]
        try:
            decoded = decode_run_agent_input(payload)
        except DecodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decoded.thread_id != PERSONA_EDITOR_THREAD_ID:
            raise HTTPException(status_code=400, detail="人格编辑 thread_id 无效")
        accept = request.headers.get("accept") or "text/event-stream"
        return StreamingResponse(
            encode_stream(
                lambda: editor().start_conversation(open_editor_session()),
                decoded.new_user_input,
                thread_id=PERSONA_EDITOR_THREAD_ID,
                run_id=decoded.run_id,
                accept=accept,
                channel="text",
                turn_coordinator=runtime.turn_coordinator,
            ),
            media_type=EventEncoder(accept=accept).get_content_type(),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.put("")
    def apply_persona(payload: PersonaApplyRequest):  # type: ignore[no-untyped-def]
        if payload.source != "guided":
            return _error(422, "persona_invalid_source", "只接受 guided 人格应用")
        try:
            result = _snapshot_payload(
                store().apply(payload.content, expected_revision=payload.expected_revision)
            )
            clear_editor_session()
            return result
        except PersonaRevisionConflictError as exc:
            return _error(409, "persona_revision_conflict", str(exc))
        except PersonaContentInvalidError as exc:
            return _error(422, "persona_invalid_content", str(exc))
        except PersonaPersistError:
            return _error(
                500,
                "persona_write_failed",
                "保存人格失败，请检查文件权限和磁盘空间后重试",
            )

    @router.post("/materialize")
    def materialize_persona():  # type: ignore[no-untyped-def]
        try:
            return _snapshot_payload(store().materialize())
        except PersonaPersistError:
            return _error(
                500,
                "persona_write_failed",
                "准备 persona.md 失败，请检查文件权限后重试",
            )

    @router.post("/undo")
    def undo_persona():  # type: ignore[no-untyped-def]
        try:
            result = _snapshot_payload(store().undo_apply())
            clear_editor_session()
            return result
        except PersonaUndoUnavailableError as exc:
            return _error(409, "persona_undo_unavailable", str(exc))
        except PersonaPersistError:
            return _error(500, "persona_write_failed", "撤销人格修改失败，请稍后重试")

    @router.delete("/customization")
    def reset_persona():  # type: ignore[no-untyped-def]
        try:
            result = _snapshot_payload(store().reset())
            clear_editor_session()
            return result
        except PersonaPersistError:
            return _error(500, "persona_write_failed", "恢复默认人格失败，请稍后重试")

    app.include_router(router)
    app.include_router(ag_ui_router)


__all__ = ["register_routes"]
