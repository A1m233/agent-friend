"""Desktop-approved social login and Agent continuation orchestration."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from agent.conversation_events import ConversationEvent, TextDelta
from agent.runtime import AgentRuntime, RunOutcome, RunPriority, SystemTriggerEvent
from agent.system_prompt import InteractionSurfaceId
from agent.tools.builtin.social_platforms import (
    SocialAuthChallenge,
    SocialAuthRequest,
    SocialAuthRequestConflictError,
    SocialAuthRequestStatus,
    SocialAuthRequiredError,
    SocialConnectorUnavailableError,
    SocialPlatformService,
    SocialProviderError,
)

if TYPE_CHECKING:
    from agent.runtime import RunHandle, RunRequest
    from agent.runtime.cancellation import CancellationToken

    from agent import Conversation

    from .protocols.im.runtime import IMRuntime

logger = logging.getLogger(__name__)


class SocialAuthOrchestrator:
    """Keep model request, desktop approval, login and continuation separate."""

    def __init__(
        self,
        *,
        service: SocialPlatformService,
        agent_runtime: AgentRuntime,
        im_runtime: IMRuntime | None = None,
    ) -> None:
        self._service = service
        self._agent_runtime = agent_runtime
        self._im_runtime = im_runtime
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.RLock()

    def get(self, request_id: str) -> SocialAuthRequest:
        return self._service.auth_request(request_id)

    def list(
        self,
        *,
        session_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[SocialAuthRequest, ...]:
        return self._service.auth_requests(session_id=session_id, active_only=active_only)

    def challenge(self, request_id: str) -> SocialAuthChallenge:
        return self._service.auth_challenge(request_id)

    def challenge_kind(self, request_id: str) -> str | None:
        return self._service.auth_challenge_kind(request_id)

    def approve(self, request_id: str) -> SocialAuthRequest:
        request = self._service.transition_auth_request(
            request_id,
            expected={"pending"},
            status="login_in_progress",
        )
        self._start_thread(
            target=self._run_login,
            args=(request.request_id,),
            name=f"SocialAuthLogin-{request.request_id[-8:]}",
        )
        return request

    def decline(self, request_id: str) -> SocialAuthRequest:
        return self._service.transition_auth_request(
            request_id,
            expected={"pending"},
            status="declined",
        )

    def cancel(self, request_id: str) -> SocialAuthRequest:
        request = self._service.auth_request(request_id)
        if request.status == "login_in_progress":
            self._service.cancel_login(request.platform)
        return self._service.transition_auth_request(
            request_id,
            expected={"pending", "login_in_progress"},
            status="cancelled",
        )

    def close(self) -> None:
        for request in self._service.auth_requests(active_only=True):
            try:
                if request.status == "login_in_progress":
                    self._service.cancel_login(request.platform)
                self._service.transition_auth_request(
                    request.request_id,
                    expected={request.status},
                    status="cancelled",
                )
            except (SocialAuthRequestConflictError, LookupError):
                continue

    def _run_login(self, request_id: str) -> None:
        try:
            request = self._service.auth_request(request_id)
            status = self._service.login(request.platform, request_id=request_id)
            if status.state != "credential_present":
                self._transition_if_current(
                    request_id,
                    expected={"login_in_progress"},
                    status="login_failed",
                    error_kind="credential_not_present",
                )
                return
            authenticated = self._service.transition_auth_request(
                request_id,
                expected={"login_in_progress"},
                status="authenticated",
            )
            self._start_continuation(authenticated)
        except SocialAuthRequestConflictError:
            # A concurrent cancel/close is authoritative.
            return
        except SocialAuthRequiredError:
            logger.exception("social login did not complete: request_id=%s", request_id)
            self._transition_if_current(
                request_id,
                expected={"login_in_progress"},
                status="login_failed",
                error_kind="auth_not_completed",
            )
        except SocialConnectorUnavailableError:
            logger.exception("social login is unavailable: request_id=%s", request_id)
            self._transition_if_current(
                request_id,
                expected={"login_in_progress"},
                status="login_failed",
                error_kind="login_unavailable",
            )
        except SocialProviderError:
            logger.exception("social provider login failed: request_id=%s", request_id)
            self._transition_if_current(
                request_id,
                expected={"login_in_progress"},
                status="login_failed",
                error_kind="provider_login_failed",
            )
        except Exception:
            logger.exception("social login failed: request_id=%s", request_id)
            self._transition_if_current(
                request_id,
                expected={"login_in_progress"},
                status="login_failed",
                error_kind="login_failed",
            )

    def _start_continuation(self, request: SocialAuthRequest) -> None:
        run_id = uuid4().hex
        prompt = _continuation_prompt(request)
        presentation: Literal["conversation", "none"] = (
            "none" if request.origin_surface.startswith("im:") else "conversation"
        )

        def runner(
            conv: Conversation,
            run_request: RunRequest,
            token: CancellationToken,
        ) -> Iterator[ConversationEvent]:
            return conv.continue_after_external_action(
                source_kind="social_auth_completed",
                system_prompt_addendum=prompt,
                presentation=presentation,
                channel=run_request.channel,
                surface=run_request.surface,
                interaction_destination_id=request.origin_destination_id,
                run_id=run_request.run_id,
                cancel_check=token.raise_if_cancelled,
            )

        try:
            continuing = self._service.transition_auth_request(
                request.request_id,
                expected={"authenticated"},
                status="continuing",
                continuation_run_id=run_id,
            )
            handle = self._agent_runtime.submit(
                SystemTriggerEvent(
                    session_id=request.session_id,
                    source_kind="social_auth_completed",
                    system_prompt_addendum=prompt,
                    output_visibility="user",
                    presentation=presentation,
                    event_metadata={"social_auth_request_id": request.request_id},
                ),
                run_id=run_id,
                priority=RunPriority.INTERACTIVE,
                dedupe_key=f"social-auth-continuation:{request.request_id}",
                channel="text",
                surface=InteractionSurfaceId(request.origin_surface),
                conversation_runner=runner,
                metadata={
                    "protocol": "social_auth",
                    "social_auth_request_id": request.request_id,
                    "interaction_destination_id": request.origin_destination_id,
                },
            )
        except Exception:
            logger.exception("failed to submit social auth continuation: %s", request.request_id)
            self._transition_if_current(
                request.request_id,
                expected={"authenticated", "continuing"},
                status="continuation_failed",
                error_kind="continuation_submit_failed",
            )
            return

        self._start_thread(
            target=self._watch_continuation,
            args=(continuing, handle),
            name=f"SocialAuthContinue-{request.request_id[-8:]}",
        )

    def _watch_continuation(self, request: SocialAuthRequest, handle: RunHandle) -> None:
        parts: list[str] = []
        try:
            for event in handle.events():
                if isinstance(event, TextDelta):
                    parts.append(event.text)
            terminal = handle.terminal
            success = terminal is not None and terminal.outcome is RunOutcome.COMPLETED
            error_kind = terminal.reason_code if terminal is not None else "missing_terminal"
            if success and request.origin_surface.startswith("im:"):
                text = "".join(parts).strip()
                if not text or not request.origin_destination_id or self._im_runtime is None:
                    success = False
                    error_kind = "im_delivery_unavailable"
                else:
                    result = self._im_runtime.send_proactive(request.origin_destination_id, text)
                    success = result.outcome == "delivered"
                    error_kind = result.error_kind or "im_delivery_failed"
            self._transition_if_current(
                request.request_id,
                expected={"continuing"},
                status="completed" if success else "continuation_failed",
                error_kind=None if success else error_kind,
            )
        except Exception:
            logger.exception("social auth continuation watcher failed: %s", request.request_id)
            self._transition_if_current(
                request.request_id,
                expected={"continuing"},
                status="continuation_failed",
                error_kind="continuation_watch_failed",
            )

    def _transition_if_current(
        self,
        request_id: str,
        *,
        expected: set[SocialAuthRequestStatus],
        status: SocialAuthRequestStatus,
        error_kind: str | None,
    ) -> None:
        try:
            self._service.transition_auth_request(
                request_id,
                expected=expected,
                status=status,
                error_kind=error_kind,
            )
        except (SocialAuthRequestConflictError, LookupError):
            return

    def _start_thread(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
        name: str,
    ) -> None:
        def run() -> None:
            try:
                target(*args)
            finally:
                with self._threads_lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, daemon=True, name=name)
        with self._threads_lock:
            self._threads.add(thread)
        thread.start()


def _continuation_prompt(request: SocialAuthRequest) -> str:
    capabilities = ", ".join(request.missing_capabilities)
    return (
        '<external_action type="social_auth_completed">\n'
        f"平台 {request.platform} 的本地登录已经由用户在电脑端完成。"
        f"此前缺失能力：{capabilities}。"
        "请结合原用户目标和最新会话上下文重新判断下一步；按需重新调用 social_search 或 "
        "social_read，也可以在已有证据足够时直接回答。不要假设旧工具调用已被自动重放，"
        "不要再次请求登录，除非新的平台结果明确表明认证仍然无效。\n"
        "</external_action>"
    )


__all__ = ["SocialAuthOrchestrator"]
