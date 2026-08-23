"""OpenAI ChatCompletion 路由集成测试。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import agent_bridge.assembly as assembly
import pytest
from agent_bridge.app import create_app_with_runtime
from agent_bridge.assembly import BridgeRuntime
from agent_bridge.settings import BridgeSettings
from fastapi.testclient import TestClient

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    NullSessionStore,
    PersonaCatalog,
    SessionManager,
    SkillCatalogService,
    make_default_registry,
)
from llm_providers import (
    LanguageModel,
    LanguageModelFactory,
    LLMStreamEvent,
    LLMTextDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
)

type ScriptedLLMItem = LLMStreamEvent | BaseException


@dataclass
class _ScriptedLLMClient:
    """与 agent 单测同模式的 scripted LLM。"""

    seen_messages: ClassVar[list[list[dict[str, Any]]]] = []
    script: list[list[ScriptedLLMItem]] = field(default_factory=list)
    turn_idx: int = 0
    context_window: int = 128000

    def generate(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        return ModelResponse(text="")

    def stream(
        self,
        request: ModelRequest,
    ) -> Iterator[LLMStreamEvent]:
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        self.seen_messages.append(request.messages)
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        for event in events:
            if isinstance(event, BaseException):
                raise event
            yield event


def _build_runtime(
    tmp_path: Path,
    *,
    llm_script: list[list[ScriptedLLMItem]],
    skill_service: SkillCatalogService | None = None,
) -> tuple[BridgeRuntime, LanguageModelFactory]:
    _ScriptedLLMClient.seen_messages = []
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions", memory_enabled=False)
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    fake_llm = _ScriptedLLMClient(script=llm_script)

    def _llm_factory(model: ModelRuntimeConfig) -> LanguageModel:
        return fake_llm

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
        skill_service=skill_service,
    )

    return (
        BridgeRuntime(
            settings=settings,
            persistent_store=persistent_store,
            transient_store=NullSessionStore(),
            persistent_session_manager=persistent_session_manager,
            catalog=catalog,
            tool_registry=tool_registry,
            prompt_builder_factory=_prompt_factory,
            default_persona=catalog.find_by_name("default").name,
            default_model="deepseek/deepseek-chat",
            skill_service=skill_service,
        ),
        _llm_factory,
    )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BridgeRuntime:
    """构造一个全新 BridgeRuntime，LLM 按脚本返回固定文本。"""
    runtime, fake_llm_factory = _build_runtime(
        tmp_path,
        llm_script=[[LLMTextDelta(text="hello from test"), LLMTurnDone(stop_reason="end_turn")]],
    )
    monkeypatch.setattr(assembly, "_llm_factory", fake_llm_factory)
    return runtime


@pytest.fixture
def client(runtime: BridgeRuntime) -> TestClient:
    app = create_app_with_runtime(runtime)
    return TestClient(app)


def _openai_payload(user_text: str) -> dict[str, Any]:
    return {
        "model": "deepseek/deepseek-chat",
        "stream": False,
        "messages": [{"role": "user", "content": user_text}],
    }


def test_structured_skill_selection_reaches_openai_model_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "skills" / "route-skill"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: route-skill\ndescription: Route integration test\n---\n\n"
        "OPENAI ROUTE SKILL BODY\n",
        encoding="utf-8",
    )
    skill_service = SkillCatalogService(
        personal_root=tmp_path / "skills",
        shared_root=tmp_path / "shared",
        watcher_enabled=False,
    )
    snapshot = skill_service.initialize()
    selected = next(item for item in snapshot.entries if item.name == "route-skill")
    runtime, fake_llm_factory = _build_runtime(
        tmp_path,
        llm_script=[[LLMTextDelta(text="ok"), LLMTurnDone(stop_reason="end_turn")]],
        skill_service=skill_service,
    )
    monkeypatch.setattr(assembly, "_llm_factory", fake_llm_factory)
    test_client = TestClient(create_app_with_runtime(runtime))
    payload = _openai_payload("use the selected workflow")
    payload["agent_friend"] = {
        "skills": [{"id": selected.id, "expected_version": selected.version}]
    }

    response = test_client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    assert any(
        "OPENAI ROUTE SKILL BODY" in item["content"] for item in _ScriptedLLMClient.seen_messages[0]
    )


class TestSessionIdHeader:
    def test_without_header_uses_transient_session_no_persistence(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        """不传 X-Agent-Friend-Session-Id 时，走无状态 transient，不写入磁盘。"""
        before_count = len(list(runtime.persistent_store.list()))
        resp = client.post("/v1/chat/completions", json=_openai_payload("hi"))
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]
        after_count = len(list(runtime.persistent_store.list()))
        assert after_count == before_count

    def test_with_header_writes_to_existing_session(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        """传 X-Agent-Friend-Session-Id 时，写入对应持久化 session。"""
        # 先通过 meta API 创建一个 session
        create_resp = client.post("/v1/sessions", json={})
        assert create_resp.status_code == 200
        sid = create_resp.json()["session_id"]

        resp = client.post(
            "/v1/chat/completions",
            json=_openai_payload("hi"),
            headers={"X-Agent-Friend-Session-Id": sid},
        )
        assert resp.status_code == 200
        response_content = resp.json()["choices"][0]["message"]["content"]
        assert response_content

        loaded = runtime.persistent_session_manager.open(sid)
        user_events = [e for e in loaded.events if e.type == "user_message"]
        assistant_events = [e for e in loaded.events if e.type == "assistant_message"]
        assert len(user_events) == 1
        assert user_events[0].payload["content"] == "hi"
        assert len(assistant_events) == 1
        assert assistant_events[0].payload["content"] == response_content

    def test_with_unknown_session_returns_404(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        """传不存在的 session_id 时，返回 404 session_not_found。"""
        resp = client.post(
            "/v1/chat/completions",
            json=_openai_payload("hi"),
            headers={"X-Agent-Friend-Session-Id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "session_not_found"

    def test_empty_user_content_returns_400(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json=_openai_payload("  "))

        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"
        assert "content 不能为空" in resp.json()["error"]["message"]

    def test_voice_headers_use_voice_prompt_for_this_turn(
        self, client: TestClient, runtime: BridgeRuntime
    ) -> None:
        create_resp = client.post("/v1/sessions", json={})
        assert create_resp.status_code == 200
        sid = create_resp.json()["session_id"]
        assert runtime.persistent_session_manager.open(sid).current_channel == "text"

        resp = client.post(
            "/v1/chat/completions",
            json=_openai_payload("hi"),
            headers={
                "X-Agent-Friend-Session-Id": sid,
                "X-Agent-Friend-Voice-Trace-Id": "trace-1",
                "X-Agent-Friend-Voice-Call-Id": "call-1",
                "X-Agent-Friend-Voice-Round-Seq": "1",
            },
        )

        assert resp.status_code == 200
        assert _ScriptedLLMClient.seen_messages
        system_prompt = _ScriptedLLMClient.seen_messages[-1][0]["content"]
        assert "正在通过电话和用户对话" in system_prompt
        assert system_prompt.count("正在通过电话和用户对话") == 1
        assert any(
            '<surface type="voice_call" />' in message.get("content", "")
            for message in _ScriptedLLMClient.seen_messages[-1]
        )
        assert runtime.persistent_session_manager.open(sid).current_channel == "text"

    def test_plain_openai_text_does_not_claim_supported_surface(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json=_openai_payload("hi"))
        assert resp.status_code == 200
        messages = _ScriptedLLMClient.seen_messages[-1]
        system_prompt = messages[0]["content"]
        assert not any(
            message.get("content", "").startswith("<interaction_context>\n") for message in messages
        )
        assert "正在通过电话和用户对话" not in system_prompt


class TestStreamingError:
    def test_voice_stream_error_returns_spoken_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, fake_llm_factory = _build_runtime(
            tmp_path,
            llm_script=[[RuntimeError("deepseek stream failed")]],
        )
        monkeypatch.setattr(assembly, "_llm_factory", fake_llm_factory)
        client = TestClient(create_app_with_runtime(runtime))
        create_resp = client.post("/v1/sessions", json={})
        assert create_resp.status_code == 200

        payload = _openai_payload("hi")
        payload["stream"] = True
        resp = client.post(
            "/v1/chat/completions",
            json=payload,
            headers={
                "X-Agent-Friend-Session-Id": create_resp.json()["session_id"],
                "X-Agent-Friend-Voice-Trace-Id": "trace-1",
                "X-Agent-Friend-Voice-Call-Id": "call-1",
                "X-Agent-Friend-Voice-Round-Seq": "1",
            },
        )

        assert resp.status_code == 200
        assert "刚才网络断了一下，你再说一遍。" in resp.text
        assert '"finish_reason": "stop"' in resp.text
        assert "data: [DONE]" in resp.text
        assert '"error"' not in resp.text

    def test_non_voice_stream_error_keeps_openai_error_chunk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime, fake_llm_factory = _build_runtime(
            tmp_path,
            llm_script=[[RuntimeError("deepseek stream failed")]],
        )
        monkeypatch.setattr(assembly, "_llm_factory", fake_llm_factory)
        client = TestClient(create_app_with_runtime(runtime))
        payload = _openai_payload("hi")
        payload["stream"] = True

        resp = client.post("/v1/chat/completions", json=payload)

        assert resp.status_code == 200
        assert '"error"' in resp.text
        assert "data: [DONE]" in resp.text
        assert "刚才网络断了一下" not in resp.text
