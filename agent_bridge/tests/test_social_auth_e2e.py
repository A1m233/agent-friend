from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from agent.runtime import AgentRuntime, RunOutcome, UserEvent
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
    SocialResolvedUrl,
    SocialResourceStore,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
    SocialSearchTool,
)
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.routes.social_auth import register_routes
from agent_bridge.social_auth import SocialAuthOrchestrator
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import (
    BUILTIN_DEFAULT_PERSONA_ID,
    Conversation,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    PersonaCatalog,
    PromptBuilder,
    Session,
    TextDelta,
    ToolRegistry,
)
from llm_providers import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
)


@dataclass(frozen=True)
class _Locator:
    platform: SocialPlatform = "xiaohongshu"
    content_type: SocialContentType = "note"
    feed_id: str = "fixture-note"


class _Connector:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    name: ClassVar[str] = "controlled-fixture"
    version: ClassVar[str] = "1"

    def __init__(self) -> None:
        self.authenticated = False
        self.search_calls = 0
        self.read_calls = 0

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        self.search_calls += 1
        if not self.authenticated:
            raise SocialAuthRequiredError("fixture credential is absent")
        return SocialSearchResponse(
            candidates=(
                SocialSearchCandidate(
                    locator=_Locator(),
                    title=f"{request.query} 的结果",
                    author="fixture-author",
                    canonical_url="https://www.xiaohongshu.com/explore/fixture-note",
                    summary="可公开看到的摘要",
                ),
            )
        )

    def read(self, locator: object) -> SocialReadResponse:
        self.read_calls += 1
        assert locator == _Locator()
        return SocialReadResponse(
            title="受控笔记",
            author="fixture-author",
            canonical_url="https://www.xiaohongshu.com/explore/fixture-note",
            evidence=(SocialEvidence(kind="note_body", text="授权后取得的受控正文证据"),),
        )

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        del url
        return None

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


@dataclass
class _ScriptedLLM:
    script: list[list[LLMStreamEvent]]
    received: list[list[dict[str, Any]]] = field(default_factory=list)
    turn_index: int = 0
    context_window: int = 128_000

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        self.received.append(request.messages)
        events = self.script[self.turn_index]
        self.turn_index += 1
        yield from events


class _IMRuntime:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_proactive(self, destination_id: str, text: str) -> OutboundSendResult:
        self.sent.append((destination_id, text))
        return OutboundSendResult("delivered")


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> list[LLMStreamEvent]:
    return [
        LLMToolCallDelta(
            index=0,
            tool_call_id=call_id,
            tool_name=name,
            args_json_delta=json.dumps(args, ensure_ascii=False),
        ),
        LLMTurnDone(stop_reason="tool_use"),
    ]


def _conversation(
    tmp_path: Path,
    *,
    llm: _ScriptedLLM,
    registry: ToolRegistry,
    session_id: str,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=tmp_path / "personas")
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(
        title="social auth e2e",
        persona="default",
        model="fixture/model",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        session_id=session_id,
    )
    store.create(session)
    prompt_builder = MarkdownPromptBuilder(
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        catalog=catalog,
    )

    def prompt_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=llm,
        context_manager=NaiveContextManager(),
        prompt_builder=prompt_builder,
        prompt_builder_factory=prompt_factory,
        catalog=catalog,
        tool_registry=registry,
        post_turn_external=True,
    )


def _approval_client(orchestrator: SocialAuthOrchestrator) -> TestClient:
    app = FastAPI()
    runtime = cast(
        BridgeRuntime,
        SimpleNamespace(social_auth_orchestrator=orchestrator),
    )
    register_routes(app, runtime)
    return TestClient(app)


@pytest.mark.parametrize(
    ("surface", "destination", "initial_notice", "surface_marker"),
    [
        (
            DESKTOP_CHAT_SURFACE,
            None,
            "需要登录，请在授权卡片确认。",
            '<surface type="desktop_chat" />',
        ),
        (
            IM_QQ_SURFACE,
            "qq|bind|c2c|chat|im:qq:chat",
            "需要登录；请回运行 agent-friend 的电脑端确认，不能在 QQ 内授权。",
            '<surface type="im" provider="qq" />',
        ),
    ],
)
def test_controlled_auth_e2e_reaches_original_goal_without_user_continue(
    tmp_path: Path,
    surface: InteractionSurfaceId,
    destination: str | None,
    initial_notice: str,
    surface_marker: str,
) -> None:
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
    llm = _ScriptedLLM(
        script=[
            _tool_call(
                "initial-search",
                "social_search",
                {"platform": "xiaohongshu", "query": "受控关键词"},
            ),
            _tool_call(
                "request-auth",
                "request_social_auth",
                {
                    "platform": "xiaohongshu",
                    "missing_capabilities": ["note_search", "note_body"],
                    "reason": "需要搜索并读取正文才能完成原目标",
                },
            ),
            [LLMTextDelta(text=initial_notice), LLMTurnDone(stop_reason="end_turn")],
            _tool_call(
                "search-after-auth",
                "social_search",
                {"platform": "xiaohongshu", "query": "受控关键词"},
            ),
            _tool_call(
                "read-after-auth",
                "social_read",
                {"resource_ref": "social_ref_00000000000000000000000000000001"},
            ),
            [
                LLMTextDelta(text="已根据授权后取得的正文完成原目标。"),
                LLMTurnDone(stop_reason="end_turn"),
            ],
        ]
    )
    session_id = "desktop-session" if destination is None else "im:qq:chat"
    conv = _conversation(
        tmp_path,
        llm=llm,
        registry=registry,
        session_id=session_id,
    )
    runtime = AgentRuntime(conversation_factory=lambda _session_id: conv, memory=None)
    im_runtime = _IMRuntime()
    orchestrator = SocialAuthOrchestrator(
        service=service,
        agent_runtime=runtime,
        im_runtime=cast(Any, im_runtime),
    )
    runtime.start()
    try:
        initial = runtime.submit(
            UserEvent(session_id=session_id, user_input="请完成这个受限平台目标"),
            channel="text",
            surface=surface,
            metadata={
                "protocol": "im" if destination else "ag_ui",
                "interaction_destination_id": destination,
            },
        )
        initial_text = "".join(
            event.text for event in initial.events() if isinstance(event, TextDelta)
        )
        assert initial.terminal is not None
        assert initial.terminal.outcome is RunOutcome.COMPLETED
        assert initial_text == initial_notice
        pending = service.auth_requests(session_id=session_id, active_only=True)
        assert len(pending) == 1
        assert pending[0].status == "pending"

        client = _approval_client(orchestrator)
        endpoint = f"/v1/social-auth/requests/{pending[0].request_id}/approve"
        assert client.post(endpoint).status_code == 422
        response = client.post(endpoint, json={"action": "approve"})
        assert response.status_code == 202

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            completed = service.auth_request(pending[0].request_id)
            if completed.status in {"completed", "continuation_failed", "login_failed"}:
                break
            time.sleep(0.01)

        assert completed.status == "completed"
        assert auth_handler.login_calls == 1
        assert connector.search_calls == 2
        assert connector.read_calls == 1
        assert llm.turn_index == 6
        assert [event.type for event in conv.session.events].count("user_message") == 1
        assert any(surface_marker in str(message.get("content", "")) for message in llm.received[0])
        assert any(
            "social_auth_completed" in str(message.get("content", ""))
            for message in llm.received[3]
        )
        if destination is None:
            assert im_runtime.sent == []
            assert any(
                event.type == "assistant_message"
                and event.payload["content"] == "已根据授权后取得的正文完成原目标。"
                for event in conv.session.events
            )
        else:
            assert im_runtime.sent == [(destination, "已根据授权后取得的正文完成原目标。")]
    finally:
        orchestrator.close()
        runtime.stop()
        service.close()
