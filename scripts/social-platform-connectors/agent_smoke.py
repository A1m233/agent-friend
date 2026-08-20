"""Real-LLM, controlled-provider smoke for social authorization continuation."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

from agent.context.naive import NaiveContextManager
from agent.conversation import Conversation
from agent.conversation_events import TextDelta, ToolCallRequest
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID, PersonaCatalog
from agent.prompts import MarkdownPromptBuilder, PromptBuilder
from agent.runtime import AgentRuntime, RunOutcome, UserEvent
from agent.sessions.session import Session
from agent.sessions.store import JsonlSessionStore
from agent.system_prompt import DESKTOP_CHAT_SURFACE, IM_QQ_SURFACE, InteractionSurfaceId
from agent.tools.builtin.social_platforms import (
    RequestSocialAuthTool,
    SocialAuthInteraction,
    SocialAuthManager,
    SocialAuthMode,
    SocialAuthRequiredError,
    SocialAuthState,
    SocialAuthStatus,
    SocialContentType,
    SocialEvidence,
    SocialPlatform,
    SocialPlatformService,
    SocialReadResponse,
    SocialReadTool,
    SocialResourceStore,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
    SocialSearchTool,
)
from agent.tools.registry import ToolRegistry
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from agent_bridge.social_auth import SocialAuthOrchestrator
from runtime_config.policy import credential_source_for_mode

from llm_providers import LanguageModel, ModelRequest, ModelResponse


@dataclass(frozen=True)
class _Locator:
    platform: SocialPlatform = "xiaohongshu"
    content_type: SocialContentType = "note"
    feed_id: str = "real-llm-controlled-note"


class _Connector:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    name: ClassVar[str] = "real-llm-controlled-connector"
    version: ClassVar[str] = "1"

    def __init__(self) -> None:
        self.authenticated = False
        self.search_calls = 0
        self.read_calls = 0

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        self.search_calls += 1
        if not self.authenticated:
            raise SocialAuthRequiredError("controlled fixture requires local login")
        return SocialSearchResponse(
            candidates=(
                SocialSearchCandidate(
                    locator=_Locator(),
                    title=f"{request.query}：受控样本",
                    author="受控作者",
                    canonical_url="https://www.xiaohongshu.com/explore/controlled-note",
                    summary="摘要不足以回答用户要求核对的正文细节。",
                ),
            )
        )

    def read(self, locator: object) -> SocialReadResponse:
        self.read_calls += 1
        if locator != _Locator():
            raise AssertionError("model read a resource outside the controlled search result")
        return SocialReadResponse(
            title="受控样本",
            author="受控作者",
            canonical_url="https://www.xiaohongshu.com/explore/controlled-note",
            evidence=(
                SocialEvidence(
                    kind="note_body",
                    text="受控正文结论：样本明确建议先验证授权续跑，再形成最终回答。",
                ),
            ),
        )

    def close(self) -> None:
        pass


class _AuthHandler:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    mode: ClassVar[SocialAuthMode] = "required"

    def __init__(self, connector: _Connector) -> None:
        self._connector = connector
        self.login_calls = 0

    def status(self) -> SocialAuthStatus:
        state: SocialAuthState = (
            "credential_present" if self._connector.authenticated else "anonymous"
        )
        return SocialAuthStatus(
            platform=self.platform,
            mode=self.mode,
            state=state,
            material_present=self._connector.authenticated,
        )

    def login(
        self,
        *,
        cancelled: Callable[[], bool],
        interaction: SocialAuthInteraction | None = None,
    ) -> SocialAuthStatus:
        del interaction
        self.login_calls += 1
        if not cancelled():
            self._connector.authenticated = True
        return self.status()

    def cancel_login(self) -> None:
        pass

    def close(self) -> None:
        pass


class _CountingClient:
    def __init__(self, inner: LanguageModel) -> None:
        self._inner = inner
        self.stream_calls = 0

    @property
    def context_window(self) -> int:
        return cast(int, self._inner.context_window)

    def generate(self, request: ModelRequest) -> ModelResponse:
        return self._inner.generate(request)

    def stream(
        self,
        request: ModelRequest,
    ) -> Any:
        self.stream_calls += 1
        return self._inner.stream(request)


class _IMRuntime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_proactive(self, destination_id: str, text: str) -> OutboundSendResult:
        self.sent.append((destination_id, text))
        return OutboundSendResult("delivered")


def _conversation(
    root: Path,
    *,
    client: _CountingClient,
    registry: ToolRegistry,
    model: str,
    session_id: str,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=root / "personas")
    store = JsonlSessionStore(root / "sessions")
    session = Session.new(
        title="072 real model social auth smoke",
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model=model,
        session_id=session_id,
    )
    store.create(session)

    def prompt_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=client,
        context_manager=NaiveContextManager(),
        prompt_builder=prompt_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=prompt_factory,
        catalog=catalog,
        tool_registry=registry,
        post_turn_external=True,
    )


def _initial_text_is_surface_aware(text: str, surface: str) -> bool:
    if surface == "desktop":
        return "登录" in text and any(word in text for word in ("授权", "确认", "卡片"))
    mentions_computer = any(word in text for word in ("电脑", "桌面", "agent-friend"))
    asks_for_secret = any(
        phrase in text.lower() for phrase in ("发送 cookie", "提供 cookie", "发送密码", "提供密码")
    )
    return mentions_computer and not asks_for_secret


def run(*, model: str, surface_name: str, timeout_seconds: float = 180) -> dict[str, object]:
    if surface_name == "desktop":
        surface = DESKTOP_CHAT_SURFACE
        session_id = "social-auth-real-llm-desktop"
        destination = None
    elif surface_name == "im:qq":
        surface = IM_QQ_SURFACE
        session_id = "social-auth-real-llm-im"
        destination = "qq|controlled-bind|c2c|controlled-chat|social-auth-real-llm-im"
    else:
        raise ValueError(f"unsupported surface: {surface_name}")

    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = _CountingClient(runtime_config.llm_client_factory(model))
    connector = _Connector()
    auth_handler = _AuthHandler(connector)
    service = SocialPlatformService(
        (connector,),
        auth=SocialAuthManager((auth_handler,)),
        resources=SocialResourceStore(
            make_ref=lambda: "social_ref_00000000000000000000000000000001"
        ),
    )
    registry = ToolRegistry(
        [SocialSearchTool(service), SocialReadTool(service), RequestSocialAuthTool(service)]
    )
    im_runtime = _IMRuntime()

    with tempfile.TemporaryDirectory(prefix="agent-friend-social-auth-llm-") as temp:
        conversation = _conversation(
            Path(temp),
            client=client,
            registry=registry,
            model=model,
            session_id=session_id,
        )
        runtime = AgentRuntime(conversation_factory=lambda _session_id: conversation, memory=None)
        orchestrator = SocialAuthOrchestrator(
            service=service,
            agent_runtime=runtime,
            im_runtime=cast(Any, im_runtime),
        )
        runtime.start()
        try:
            initial_handle = runtime.submit(
                UserEvent(
                    session_id=session_id,
                    user_input=(
                        "请在小红书搜索“授权续跑受控样本”，并且必须读取候选笔记正文后，"
                        "用正文证据回答它建议先做什么。摘要不足以回答；若工具说明需要登录，"
                        "请根据证据缺口和当前交互端处理，不要改用普通网页搜索。"
                    ),
                ),
                channel="text",
                surface=cast(InteractionSurfaceId, surface),
                metadata={
                    "protocol": "im" if destination else "ag_ui",
                    "interaction_destination_id": destination,
                },
            )
            initial_events = list(initial_handle.events())
            if (
                initial_handle.terminal is None
                or initial_handle.terminal.outcome is not RunOutcome.COMPLETED
            ):
                raise AssertionError("initial real-Agent run did not complete")
            initial_text = "".join(
                event.text for event in initial_events if isinstance(event, TextDelta)
            )
            initial_tools = [
                event.tool_name for event in initial_events if isinstance(event, ToolCallRequest)
            ]
            pending = service.auth_requests(session_id=session_id, active_only=True)
            if len(pending) != 1 or pending[0].status != "pending":
                raise AssertionError(
                    f"Agent did not create one pending auth request; tools={initial_tools!r}"
                )
            if not _initial_text_is_surface_aware(initial_text, surface_name):
                raise AssertionError(
                    "Agent auth explanation did not respect the interaction surface"
                )

            orchestrator.approve(pending[0].request_id)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                request = service.auth_request(pending[0].request_id)
                if request.status in {"completed", "continuation_failed", "login_failed"}:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("authorization continuation timed out")
            if request.status != "completed":
                raise AssertionError(
                    f"authorization continuation ended with status={request.status}"
                )

            trigger_index = next(
                index
                for index, event in enumerate(conversation.session.events)
                if event.type == "system_trigger"
                and event.payload.get("source_kind") == "social_auth_completed"
            )
            continuation_events = conversation.session.events[trigger_index + 1 :]
            continuation_tools = [
                str(event.payload.get("tool_name"))
                for event in continuation_events
                if event.type == "tool_call_request"
            ]
            if "social_search" not in continuation_tools or "social_read" not in continuation_tools:
                raise AssertionError(
                    f"Agent did not replan through search and read: {continuation_tools!r}"
                )
            if [event.type for event in conversation.session.events].count("user_message") != 1:
                raise AssertionError(
                    "continuation incorrectly required or forged another user message"
                )

            if destination is None:
                final_text = next(
                    str(event.payload.get("content", ""))
                    for event in reversed(conversation.session.events)
                    if event.type == "assistant_message"
                )
                delivered_to_origin = bool(final_text.strip()) and not im_runtime.sent
            else:
                delivered_to_origin = bool(
                    im_runtime.sent
                    and im_runtime.sent[-1][0] == destination
                    and im_runtime.sent[-1][1].strip()
                )
                final_text = im_runtime.sent[-1][1] if im_runtime.sent else ""
            if not delivered_to_origin:
                raise AssertionError("final answer was not delivered to the frozen origin surface")

            return {
                "status": "passed",
                "model": model,
                "surface": surface_name,
                "real_llm_stream_calls": client.stream_calls,
                "initial_tool_sequence": initial_tools,
                "continuation_tool_sequence": continuation_tools,
                "surface_aware_explanation": True,
                "login_calls": auth_handler.login_calls,
                "search_calls": connector.search_calls,
                "read_calls": connector.read_calls,
                "single_user_message": True,
                "delivered_to_origin": delivered_to_origin,
                "initial_reply_chars": len(initial_text),
                "final_reply_chars": len(final_text),
            }
        finally:
            orchestrator.close()
            runtime.stop()
            service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--surface", choices=("desktop", "im:qq"), required=True)
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="required acknowledgement that this smoke sends about six real LLM requests",
    )
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("real LLM calls require explicit --allow-llm")
    print(
        json.dumps(
            run(model=args.model, surface_name=args.surface),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = ["run"]
