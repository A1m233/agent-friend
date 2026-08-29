from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from agent import BUILTIN_DEFAULT_PERSONA_ID, Event, JsonlSessionStore, Session
from llm_providers import default_model_runtime_config


def _load_module() -> ModuleType:
    path = Path(__file__).parents[1] / "prompt-cache-smoke" / "run.py"
    spec = importlib.util.spec_from_file_location("agent_friend_prompt_cache_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_client_for_model_passes_runtime_config_to_bridge_factory() -> None:
    module = _load_module()
    received: list[Any] = []
    sentinel = object()

    class RuntimeConfig:
        @staticmethod
        def llm_client_factory(config: Any) -> object:
            received.append(config)
            return sentinel

    model = "deepseek/deepseek-v4-flash-vision-exp"
    assert module._client_for_model(RuntimeConfig(), model) is sentinel
    assert received == [default_model_runtime_config(model)]


def test_deepseek_production_shape_matches_observed_request_dimensions() -> None:
    module = _load_module()

    messages = module._deepseek_base("shape", 120_000, production_shape=True)
    tools = module._deepseek_tools("shape")

    assert len(messages) == 184
    assert len(tools) == 26
    assert len({tool["function"]["name"] for tool in tools}) == 26


def test_deepseek_shape_matrix_uses_exact_pairs_and_eight_requests(monkeypatch: Any) -> None:
    module = _load_module()
    calls: list[tuple[str, str, int, int, int]] = []

    monkeypatch.setattr(module, "resolve_bridge_runtime_config", lambda _source: object())
    monkeypatch.setattr(module, "credential_source_for_mode", lambda _mode: object())
    monkeypatch.setattr(module, "_client_for_model", lambda _config, _model: object())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_request(
        _client: object,
        *,
        scenario: str,
        phase: str,
        messages: list[dict[str, Any]],
        prompt_cache_key: str | None,
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
    ) -> Any:
        assert prompt_cache_key is None
        calls.append((scenario, phase, len(messages), len(tools or []), max_tokens))
        return module._Observation(
            scenario=scenario,
            phase=phase,
            prompt_tokens=1,
            cached_prompt_tokens=0,
            cache_miss_prompt_tokens=1,
            cached_prompt_tokens_source="test",
            cache_miss_prompt_tokens_source="test",
            request_fingerprint="fingerprint",
            first_text_ms=1.0,
            total_ms=2.0,
        )

    monkeypatch.setattr(module, "_request", fake_request)

    result = module._run_deepseek_shape_matrix(
        model="deepseek/deepseek-v4-flash-vision-exp",
        history_chars=120_000,
        settle_seconds=5,
    )

    assert result["real_llm_calls"] == 8
    assert calls == [
        ("compact_messages_without_tools", "warm", 2, 0, 32),
        ("compact_messages_without_tools", "probe", 2, 0, 32),
        ("compact_messages_with_tools", "warm", 2, 26, 32),
        ("compact_messages_with_tools", "probe", 2, 26, 32),
        ("many_messages_without_tools", "warm", 184, 0, 32),
        ("many_messages_without_tools", "probe", 184, 0, 32),
        ("many_messages_with_tools", "warm", 184, 26, 32),
        ("many_messages_with_tools", "probe", 184, 26, 32),
    ]


def test_deepseek_tool_loop_ab_uses_real_replay_and_four_requests(monkeypatch: Any) -> None:
    module = _load_module()
    calls: list[tuple[str, str, list[dict[str, Any]], int]] = []

    monkeypatch.setattr(module, "resolve_bridge_runtime_config", lambda _source: object())
    monkeypatch.setattr(module, "credential_source_for_mode", lambda _mode: object())
    monkeypatch.setattr(module, "_client_for_model", lambda _config, _model: object())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_tool_turn_request(
        _client: object,
        *,
        scenario: str,
        phase: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_request_witness: Any = None,
    ) -> Any:
        calls.append((scenario, phase, messages, len(tools)))
        call_id = f"{scenario}-{phase}"
        return module._ToolTurnResult(
            observation=module._Observation(
                scenario=scenario,
                phase=phase,
                prompt_tokens=1,
                cached_prompt_tokens=0,
                cache_miss_prompt_tokens=1,
                cached_prompt_tokens_source="test",
                cache_miss_prompt_tokens_source="test",
                request_fingerprint=call_id,
                first_text_ms=None,
                total_ms=1.0,
            ),
            assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "cache_probe_tool_00",
                            "arguments": '{"query":"cache-check"}',
                        },
                    }
                ],
            },
            tool_message={
                "role": "tool",
                "tool_call_id": call_id,
                "content": '{"ok":true}',
            },
            request_witness=call_id,
        )

    monkeypatch.setattr(module, "_tool_turn_request", fake_tool_turn_request)

    result = module._run_deepseek_tool_loop_ab(
        model="deepseek/deepseek-v4-flash-vision-exp",
        history_chars=120_000,
        settle_seconds=5,
    )

    assert result["real_llm_calls"] == 4
    assert len(calls) == 4
    assert all(tool_count == 26 for _, _, _, tool_count in calls)
    assert result["first_request_prefix_preserved_in_probe"] == {
        "current_tail_rebuild": False,
        "provider_transcript_continuation": True,
    }
    current_warm = calls[0][2]
    current_probe = calls[1][2]
    ledger_warm = calls[2][2]
    ledger_probe = calls[3][2]
    assert current_probe[: len(current_warm)] != current_warm
    assert ledger_probe[: len(ledger_warm)] == ledger_warm
    assert calls[1][1] == calls[3][1] == "probe_after_tool"
    assert result["acceptance"]["scenario"] == "provider_transcript_continuation"


def test_deepseek_cross_user_turn_mode_routes_to_conversation_smoke(
    monkeypatch: Any,
) -> None:
    module = _load_module()
    received: list[dict[str, Any]] = []

    def fake_cross_user_turn(**kwargs: Any) -> dict[str, Any]:
        received.append(dict(kwargs))
        return {"experiment": "cross-user-turn"}

    monkeypatch.setattr(module, "_run_deepseek_cross_user_turn", fake_cross_user_turn)

    result = module.run(
        model="deepseek/deepseek-v4-flash-vision-exp",
        history_chars=24_000,
        settle_seconds=0,
        deepseek_cross_user_turn=True,
    )

    assert result == {"experiment": "cross-user-turn"}
    assert received == [
        {
            "model": "deepseek/deepseek-v4-flash-vision-exp",
            "history_chars": 24_000,
        }
    ]


def test_long_session_selector_uses_safe_ref_and_content_free_stats(tmp_path: Path) -> None:
    module = _load_module()
    model = "deepseek/deepseek-v4-flash"
    store = JsonlSessionStore(tmp_path / "sessions")
    session = Session.new(
        title="private-title",
        persona="default",
        model=model,
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
    )
    store.create(session)
    private_fragment = "private-history-fragment"
    for index in range(5):
        for event_type, content in (
            ("user_message", f"user-{index}:" + private_fragment * 80),
            ("assistant_message", f"assistant-{index}:" + private_fragment * 80),
        ):
            event = Event(
                type=event_type,
                uuid=str(uuid4()),
                ts=datetime.now(UTC),
                payload={"content": content},
                meta={"model": model},
            )
            store.append_event(session.session_id, event)
            session.append(event)

    safe_ref = module._safe_session_ref(session.session_id)
    expected_ref = hashlib.sha256(
        ("prompt-cache-smoke\0" + session.session_id).encode()
    ).hexdigest()[:12]
    selected, stats = module._select_long_session(
        store=store,
        model=model,
        source_session_ref=safe_ref,
        min_history_chars=8_000,
    )

    assert selected.session_id == session.session_id
    assert safe_ref == expected_ref
    assert stats.session_ref == safe_ref
    assert stats.complete_turns == 5
    assert stats.attachments == 0
    serialized_stats = str(asdict(stats))
    assert session.session_id not in serialized_stats
    assert private_fragment not in serialized_stats
    assert "private-title" not in serialized_stats


def test_deepseek_long_session_mode_routes_safe_ref(monkeypatch: Any) -> None:
    module = _load_module()
    received: list[dict[str, Any]] = []

    def fake_long_session(**kwargs: Any) -> dict[str, Any]:
        received.append(dict(kwargs))
        return {"experiment": "long-session-dialogue"}

    monkeypatch.setattr(module, "_run_deepseek_long_session_dialogue", fake_long_session)

    result = module.run(
        model="deepseek/deepseek-v4-flash",
        history_chars=8_000,
        settle_seconds=0,
        deepseek_long_session_dialogue=True,
        source_session_ref="safe-ref",
        dialogue_turns=3,
    )

    assert result == {"experiment": "long-session-dialogue"}
    assert received == [
        {
            "model": "deepseek/deepseek-v4-flash",
            "history_chars": 8_000,
            "source_session_ref": "safe-ref",
            "dialogue_turns": 3,
        }
    ]


def test_deepseek_dynamic_context_mode_routes_safe_ref(monkeypatch: Any) -> None:
    module = _load_module()
    received: list[dict[str, Any]] = []

    def fake_dynamic_context(**kwargs: Any) -> dict[str, Any]:
        received.append(dict(kwargs))
        return {"experiment": "dynamic-context-dialogue"}

    monkeypatch.setattr(
        module,
        "_run_deepseek_dynamic_context_dialogue",
        fake_dynamic_context,
    )

    result = module.run(
        model="deepseek/deepseek-v4-flash",
        history_chars=8_000,
        settle_seconds=0,
        deepseek_dynamic_context_dialogue=True,
        source_session_ref="safe-ref",
    )

    assert result == {"experiment": "dynamic-context-dialogue"}
    assert received == [
        {
            "model": "deepseek/deepseek-v4-flash",
            "history_chars": 8_000,
            "source_session_ref": "safe-ref",
        }
    ]
