from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from agent.runtime import (
    AgentRuntime,
    RunHandle,
    RunOutcome,
    RunRequest,
    SystemTriggerEvent,
)
from agent.tools.builtin.social_platforms import (
    SocialAuthChallenge,
    SocialAuthInteraction,
    SocialAuthManager,
    SocialAuthMode,
    SocialAuthRequestConflictError,
    SocialAuthState,
    SocialAuthStatus,
    SocialPlatform,
    SocialPlatformService,
)
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.routes.social_auth import register_routes
from agent_bridge.social_auth import SocialAuthOrchestrator
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import TextDelta


class _AuthHandler:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    mode: ClassVar[SocialAuthMode] = "required"

    def __init__(self, *, login_state: SocialAuthState = "credential_present") -> None:
        self.login_state = login_state
        self.login_calls = 0
        self.cancel_calls = 0
        self.close_calls = 0

    def status(self) -> SocialAuthStatus:
        return self._status("anonymous")

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        del interaction
        self.login_calls += 1
        if cancelled():
            return self._status("anonymous")
        return self._status(self.login_state)

    def cancel_login(self) -> None:
        self.cancel_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def _status(self, state: SocialAuthState) -> SocialAuthStatus:
        return SocialAuthStatus(
            platform=self.platform,
            mode="required",
            state=state,
            material_present=state == "credential_present",
        )


class _ChallengeAuthHandler:
    platform: ClassVar[SocialPlatform] = "bilibili"
    mode: ClassVar[SocialAuthMode] = "optional"

    def __init__(self) -> None:
        self.published = threading.Event()
        self.release = threading.Event()

    def status(self) -> SocialAuthStatus:
        return SocialAuthStatus(
            platform=self.platform,
            mode=self.mode,
            state="anonymous",
            material_present=False,
        )

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        assert interaction is not None
        interaction.publish_challenge(
            SocialAuthChallenge(
                kind="qr_image",
                media_type="image/png",
                content=b"fixture-qr-image",
            )
        )
        self.published.set()
        self.release.wait(timeout=2)
        interaction.clear_challenge()
        if cancelled():
            raise RuntimeError("cancelled")
        return SocialAuthStatus(
            platform=self.platform,
            mode=self.mode,
            state="credential_present",
            material_present=True,
        )

    def cancel_login(self) -> None:
        self.release.set()

    def close(self) -> None:
        self.release.set()


class _Runtime:
    def __init__(self, *, response: str = "续跑完成") -> None:
        self.response = response
        self.submissions: list[tuple[object, dict[str, Any]]] = []

    def submit(self, event: SystemTriggerEvent, **kwargs: Any) -> RunHandle:
        self.submissions.append((event, kwargs))
        request = RunRequest(
            event=event,
            run_id=kwargs["run_id"],
            priority=kwargs["priority"],
            dedupe_key=kwargs["dedupe_key"],
            channel=kwargs["channel"],
            surface=kwargs["surface"],
            conversation_runner=kwargs["conversation_runner"],
            metadata=kwargs["metadata"],
        )
        handle = RunHandle(request)
        handle.mark_running()
        handle.emit(TextDelta(self.response))
        handle.finish_once(RunOutcome.COMPLETED)
        return handle


class _IMRuntime:
    def __init__(self, *, outcome: str = "delivered") -> None:
        self.outcome = outcome
        self.sent: list[tuple[str, str]] = []

    def send_proactive(self, destination_id: str, text: str) -> OutboundSendResult:
        self.sent.append((destination_id, text))
        return OutboundSendResult(cast(Any, self.outcome))


def _service(handler: _AuthHandler) -> SocialPlatformService:
    return SocialPlatformService((), auth=SocialAuthManager((handler,)))


def _run_threads_inline(
    orchestrator: SocialAuthOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*, target: Callable[..., None], args: tuple[object, ...], name: str) -> None:
        del name
        target(*args)

    monkeypatch.setattr(orchestrator, "_start_thread", run)


def test_im_approval_logs_in_continues_same_session_and_returns_to_frozen_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _AuthHandler()
    service = _service(handler)
    runtime = _Runtime(response="这是授权后完成的答案")
    im_runtime = _IMRuntime()
    orchestrator = SocialAuthOrchestrator(
        service=service,
        agent_runtime=cast(AgentRuntime, runtime),
        im_runtime=cast(Any, im_runtime),
    )
    _run_threads_inline(orchestrator, monkeypatch)
    destination = "qq|bind|c2c|chat|im:qq:chat"
    request = service.request_auth(
        session_id="im:qq:chat",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="im:qq",
        origin_destination_id=destination,
    )

    orchestrator.approve(request.request_id)

    completed = orchestrator.get(request.request_id)
    assert completed.status == "completed"
    assert handler.login_calls == 1
    assert im_runtime.sent == [(destination, "这是授权后完成的答案")]
    event, kwargs = runtime.submissions[0]
    assert event.session_id == "im:qq:chat"  # type: ignore[attr-defined]
    assert event.presentation == "none"  # type: ignore[attr-defined]
    assert str(kwargs["surface"]) == "im:qq"
    assert kwargs["metadata"]["interaction_destination_id"] == destination
    assert kwargs["conversation_runner"] is not None
    assert completed.continuation_run_id == kwargs["run_id"]


def test_desktop_approval_keeps_continuation_in_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _AuthHandler()
    service = _service(handler)
    runtime = _Runtime()
    orchestrator = SocialAuthOrchestrator(
        service=service,
        agent_runtime=cast(AgentRuntime, runtime),
    )
    _run_threads_inline(orchestrator, monkeypatch)
    request = service.request_auth(
        session_id="desktop-session",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )

    orchestrator.approve(request.request_id)

    event, kwargs = runtime.submissions[0]
    assert orchestrator.get(request.request_id).status == "completed"
    assert event.presentation == "conversation"  # type: ignore[attr-defined]
    assert str(kwargs["surface"]) == "desktop_chat"
    assert kwargs["metadata"]["interaction_destination_id"] is None


def test_decline_cancel_and_login_failure_never_submit_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _AuthHandler(login_state="anonymous")
    service = _service(handler)
    runtime = _Runtime()
    orchestrator = SocialAuthOrchestrator(
        service=service,
        agent_runtime=cast(AgentRuntime, runtime),
    )
    _run_threads_inline(orchestrator, monkeypatch)

    declined = service.request_auth(
        session_id="declined",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )
    assert orchestrator.decline(declined.request_id).status == "declined"

    cancelled = service.request_auth(
        session_id="cancelled",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )
    assert orchestrator.cancel(cancelled.request_id).status == "cancelled"

    failed = service.request_auth(
        session_id="failed",
        platform="xiaohongshu",
        missing_capabilities=("note_body",),
        reason="需要正文",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )
    orchestrator.approve(failed.request_id)

    assert orchestrator.get(failed.request_id).status == "login_failed"
    assert handler.login_calls == 1
    assert runtime.submissions == []
    with pytest.raises(SocialAuthRequestConflictError):
        orchestrator.approve(declined.request_id)


def test_bilibili_challenge_is_loopback_only_no_store_and_cleared_on_cancel() -> None:
    handler = _ChallengeAuthHandler()
    service = SocialPlatformService((), auth=SocialAuthManager((handler,)))
    runtime = _Runtime()
    orchestrator = SocialAuthOrchestrator(
        service=service,
        agent_runtime=cast(AgentRuntime, runtime),
    )
    app = FastAPI()
    register_routes(
        app,
        cast(BridgeRuntime, SimpleNamespace(social_auth_orchestrator=orchestrator)),
    )
    client = TestClient(app)
    request = service.request_auth(
        session_id="desktop-bilibili",
        platform="bilibili",
        missing_capabilities=("video_subtitle",),
        reason="需要字幕",
        origin_surface="desktop_chat",
        origin_destination_id=None,
    )

    approve = client.post(
        f"/v1/social-auth/requests/{request.request_id}/approve",
        json={"action": "approve"},
    )
    assert approve.status_code == 202
    assert handler.published.wait(timeout=1)

    snapshot = client.get(f"/v1/social-auth/requests/{request.request_id}")
    assert snapshot.status_code == 200
    assert snapshot.json()["challengeKind"] == "qr_image"
    challenge = client.get(f"/v1/social-auth/requests/{request.request_id}/challenge")
    assert challenge.status_code == 200
    assert challenge.content == b"fixture-qr-image"
    assert challenge.headers["content-type"] == "image/png"
    assert challenge.headers["cache-control"] == "no-store"

    cancelled = client.post(
        f"/v1/social-auth/requests/{request.request_id}/cancel",
        json={"action": "cancel"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["challengeKind"] is None
    assert client.get(f"/v1/social-auth/requests/{request.request_id}/challenge").status_code == 404
    orchestrator.close()
    service.close()
