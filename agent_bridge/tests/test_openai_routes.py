"""OpenAI ChatCompletion 路由集成测试。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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
    make_default_registry,
)
from llm_providers import LLMClient, LLMStreamEvent, LLMTextDelta, LLMTurnDone

LLMClientFactory = Callable[[str], LLMClient]


@dataclass
class _ScriptedLLMClient:
    """与 agent 单测同模式的 scripted LLM。"""

    script: list[list[LLMStreamEvent]] = field(default_factory=list)
    turn_idx: int = 0
    context_window: int = 128000

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:  # pragma: no cover
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        if self.turn_idx >= len(self.script):
            yield LLMTurnDone(stop_reason="end_turn")
            return
        events = self.script[self.turn_idx]
        self.turn_idx += 1
        yield from events


def _build_runtime(
    tmp_path: Path,
    *,
    llm_script: list[list[LLMStreamEvent]],
) -> tuple[BridgeRuntime, LLMClientFactory]:
    settings = BridgeSettings(sessions_dir=tmp_path / "sessions", memory_enabled=False)
    persistent_store = JsonlSessionStore(settings.sessions_dir)
    catalog = PersonaCatalog()
    tool_registry = make_default_registry()

    fake_llm = _ScriptedLLMClient(script=llm_script)

    def _llm_factory(model: str) -> LLMClient:
        return cast(LLMClient, fake_llm)

    def _prompt_factory(persona_id: str) -> MarkdownPromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id)

    persistent_session_manager = SessionManager(
        store=persistent_store,
        llm_client_factory=_llm_factory,
        prompt_builder_factory=_prompt_factory,
        context_manager_factory=NaiveContextManager,
        tool_registry=tool_registry,
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
