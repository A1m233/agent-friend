"""One submission path for desktop and scheduled inputs."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from agent.conversation_events import ConversationEvent
from agent.message_references import QuotedTextReference
from agent.paths import session_requests_db_path
from agent.runtime import (
    ConsumerAbandonPolicy,
    RunHandle,
    RunPriority,
    RunStatus,
    UserEvent,
)
from agent.runtime.models import RunRequest
from agent.session_requests import RequestOptions, SessionRequestStore
from agent.session_requests.errors import SessionRequestError
from agent.sessions import SessionNotFoundError
from agent.skills import SkillSelection, SkillTurnSnapshot
from agent.system_prompt.surfaces import DESKTOP_CHAT_SURFACE
from fastapi import HTTPException

from agent import Conversation

from ..attachment_inputs import ExistingAttachmentInput, prepare_run_attachment_input
from ..errors import map_exception

if TYPE_CHECKING:
    from ..assembly import BridgeRuntime
    from ..session_bridge import PersistentBootstrap


class SessionRequestService:
    def __init__(self, runtime: BridgeRuntime) -> None:
        self.runtime = runtime
        self.store = SessionRequestStore(
            runtime.settings.session_requests_db
            or session_requests_db_path(runtime.settings.sessions_dir.parent)
        )
        self._lock = threading.RLock()
        self._handles: dict[str, RunHandle] = {}
        self.store.recover(self._terminal)
        if runtime.agent_runtime is not None:
            runtime.agent_runtime.add_run_observer(self.store.observe)
            runtime.agent_runtime.add_terminal_observer(self._finished)

    def _terminal(self, session_id: str, run_id: str) -> tuple[str, str] | None:
        try:
            session = self.runtime.persistent_session_manager.query(session_id)
        except SessionNotFoundError:
            return None
        for event in reversed(session.events):
            if event.type == "run_terminal" and event.meta.get("run_id") == run_id:
                return str(event.payload.get("status", "interrupted")), str(
                    event.payload.get("reason_code", "")
                )
        return None

    def _finished(self, snapshot: Any) -> None:
        # Never take admission lock from the scheduler's callback thread.
        self._handles.pop(snapshot.run_id, None)

    def terminal_receipt(self, run_id: str) -> tuple[str, str] | None:
        receipt = self.store.get(run_id)
        if receipt and receipt["status"] == "terminal":
            return str(receipt["outcome"]), str(receipt["reason_code"])
        return None

    def validate_target(self, session_id: str) -> None:
        manager = self.runtime.persistent_session_manager
        try:
            manager.query(session_id)
        except SessionNotFoundError as exc:
            raise SessionRequestError(
                "target_session_missing", "目标会话已不存在，请选择其它会话。"
            ) from exc
        if manager.is_archived(session_id):
            raise SessionRequestError(
                "target_session_archived", "目标会话已归档，请先取消归档再恢复任务。"
            )

    def submit(
        self,
        *,
        boot: PersistentBootstrap,
        run_id: str,
        user_event_id: str,
        options: RequestOptions | None = None,
        source: dict[str, Any] | None = None,
        references: tuple[QuotedTextReference, ...] = (),
        skills: tuple[SkillSelection, ...] = (),
        skill_snapshot: SkillTurnSnapshot | None = None,
        allow_create: bool = False,
        latency_started_ms: int | None = None,
        supersede_background: bool = True,
        detached: bool = False,
    ) -> RunHandle:
        options = options or RequestOptions()
        runtime = self.runtime
        agent_runtime = runtime.agent_runtime
        if agent_runtime is None:
            raise HTTPException(503, detail="runtime unavailable")
        payload = {
            "text": boot.new_user_input,
            "options": options.to_dict(),
            "source": source,
            "references": [asdict(ref) for ref in references],
            "skills": [asdict(skill) for skill in skills],
        }
        with self._lock:
            existing = self.store.get(run_id)
            if existing is not None:
                self.store.admit(run_id, boot.thread_id, user_event_id, payload)
                handle = self._handles.get(run_id)
                if handle is not None:
                    raise HTTPException(
                        409, detail={"error": "request_already_accepted", "run_id": run_id}
                    )
                raise HTTPException(
                    409, detail={"error": "request_already_finished", "run_id": run_id}
                )
            if not allow_create or runtime.persistent_session_manager.exists(boot.thread_id):
                self.validate_target(boot.thread_id)
            self.store.admit(run_id, boot.thread_id, user_event_id, payload)

            def prepare(cancel_check: Any) -> Conversation:
                cancel_check()
                if not allow_create or runtime.persistent_session_manager.exists(boot.thread_id):
                    self.validate_target(boot.thread_id)
                from ..session_bridge import SessionBridge

                conv = SessionBridge(runtime).bind_persistent(
                    boot, post_turn_external=True, cancel_check=cancel_check
                )
                prepare_run_attachment_input(
                    [ExistingAttachmentInput(ref) for ref in options.attachments],
                    model_id=options.model.model_id if options.model else conv.current_model,
                    store=runtime.attachment_store,
                    capability_resolver=runtime.attachment_capabilities,
                    session_files_available=runtime.session_files_available,
                    cancel_check=cancel_check,
                )
                return conv

            def run(
                conv: Conversation, request: RunRequest, token: Any
            ) -> Iterator[ConversationEvent]:
                broker = runtime.approval_broker
                scope = (
                    broker.run_mode(boot.thread_id, run_id, options.approval_mode)
                    if broker
                    else nullcontext()
                )
                with scope:
                    self.store.record_effective_options(
                        run_id,
                        RequestOptions(
                            attachments=options.attachments,
                            model=options.model or conv.current_model_config,
                            approval_mode=broker.mode_state(boot.thread_id, run_id).mode
                            if broker
                            else "per_request",
                        ).to_dict(),
                    )
                    yield from conv.stream(
                        boot.new_user_input,
                        attachments=list(options.attachments),
                        references=list(references),
                        user_event_id=user_event_id,
                        channel=request.channel,
                        surface=request.surface,
                        run_id=run_id,
                        cancel_check=token.raise_if_cancelled,
                        skill_selections=skills,
                        skill_turn_snapshot=skill_snapshot,
                        model_override=options.model,
                        request_source=source,
                        approval_mode_override=(
                            broker.mode_state(boot.thread_id, run_id)
                            if broker and options.approval_mode
                            else None
                        ),
                    )

            def persist_error(conv: Conversation, exc: BaseException, request: RunRequest) -> None:
                err = map_exception(exc)
                conv.append_run_error_event(
                    code=err.code,
                    message=err.message,
                    recoverable=err.recoverable,
                    source="session_request",
                    run_id=request.run_id,
                    sanitize_failed_model_call=err.sanitize_failed_model_call,
                    reason=err.reason,
                    recovery_action=err.recovery_action,
                )

            try:
                handle = agent_runtime.submit(
                    UserEvent(session_id=boot.thread_id, user_input=boot.new_user_input),
                    run_id=run_id,
                    priority=RunPriority.INTERACTIVE,
                    replaceable=False,
                    supersede_lower_priority=supersede_background,
                    channel="text",
                    surface=DESKTOP_CHAT_SURFACE,
                    latency_started_ms=latency_started_ms,
                    conversation_preparer=prepare,
                    conversation_runner=run,
                    error_callback=persist_error,
                    consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                    metadata={"protocol": "session_request", "detached": detached},
                    control_scope="desktop_text",
                    input_accepted=False,
                )
            except BaseException:
                self.store.settle(run_id, "rejected", "runtime_submit_failed")
                raise
            if handle.status is not RunStatus.TERMINAL:
                self._handles[run_id] = handle
            self.store.observe(handle.snapshot())
            return handle

    def cancel(self, session_id: str, run_id: str) -> Any:
        runtime = self.runtime
        assert runtime.agent_runtime is not None
        result, snapshot = runtime.agent_runtime.cancel_run(
            run_id, expected_session_id=session_id, required_control_scope="desktop_text"
        )
        if (
            snapshot is not None
            and snapshot.session_id == session_id
            and snapshot.control_scope == "desktop_text"
        ):
            if runtime.approval_broker:
                runtime.approval_broker.cancel_run(session_id, run_id)
            if runtime.command_runtime:
                runtime.command_runtime.terminate_run(session_id, run_id)
        return result, snapshot
