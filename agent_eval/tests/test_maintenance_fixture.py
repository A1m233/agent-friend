from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent_eval.judge import judge_case
from agent_eval.loader import load_contracts
from agent_eval.maintenance_fixture import parse_fixture
from agent_eval.schema import (
    Case,
    FixtureSemanticMemory,
    ToolCallTrace,
    Trace,
)

from llm_providers import (
    LanguageModelCapabilities,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ProviderAssistantTurn,
    ProviderSpec,
    ProviderToolCall,
)


@pytest.mark.parametrize(
    "raw",
    [
        "bad",
        [{"id": "x"}],
        [{"id": "x", "statement": "x", "db_path": "real.db"}],
        [{"id": "x", "statement": "x", "speaker": "system"}],
        [{"id": "x", "statement": "x"}, {"id": "x", "statement": "another"}],
    ],
)
def test_invalid_memory_fixture_is_rejected(raw: Any) -> None:
    with pytest.raises(ValueError):
        parse_fixture(raw, label="fixture")


@pytest.mark.parametrize("late_failure", [False, True])
def test_runner_uses_real_inventory_in_an_isolated_store(
    monkeypatch: pytest.MonkeyPatch, late_failure: bool
) -> None:
    import agent_eval.runner as runner

    class Model:
        context_window = 128000
        capabilities = LanguageModelCapabilities()
        calls = 0

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            self.calls += 1
            if self.calls == 1:
                encoded = "{}"
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="inventory",
                    tool_name="semantic_memory_query",
                    args_json_delta=encoded,
                )
                yield LLMTurnDone(
                    stop_reason="tool_use",
                    assistant_turn=ProviderAssistantTurn(
                        content=None,
                        tool_calls=(
                            ProviderToolCall(
                                index=0,
                                tool_call_id="inventory",
                                tool_name="semantic_memory_query",
                                arguments_json=encoded,
                            ),
                        ),
                    ),
                )
            else:
                result = json.loads(
                    next(m["content"] for m in reversed(request.messages) if m["role"] == "tool")
                )
                assert [v["semantic_id"] for v in result["items"]] == ["ordinary"]
                if late_failure:
                    from agent.context.errors import ContextCompactionUnavailableError

                    yield LLMTextDelta(text="库存已读，正在核对。")
                    raise ContextCompactionUnavailableError(
                        "fixture failure", reason="checkpoint_unsupported"
                    )
                yield LLMTurnDone(
                    stop_reason="end_turn",
                    assistant_turn=ProviderAssistantTurn(content="已取得普通库存"),
                )

    model = Model()
    monkeypatch.setattr(runner, "LLMClient", lambda spec: model)
    trace = runner.run_case(
        Case(
            id="memory-fixture",
            query="用 maintain-af-memory 查库存",
            facet="fixture",
            source="synthetic",
            fixture_semantic_memory=[
                FixtureSemanticMemory("ordinary", "ordinary fact", "source"),
                FixtureSemanticMemory("pinned", "other layer", pinned=True),
            ],
        ),
        ProviderSpec(model="openai/test", api_key="fake"),
    )
    if late_failure:
        assert trace.error is not None and "reason=checkpoint_unsupported" in trace.error
        assert trace.stop_reason == "error"
        assert "库存已读" in trace.final_text
        assert trace.assistant_segments
        assert trace.skill_activations
    else:
        assert trace.error is None
    assert trace.tool_calls[0].name == "semantic_memory_query"
    assert not trace.tool_calls[0].is_error


def test_maintenance_fixture_preserves_schema_and_blocks_external_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.tools.builtin.web_fetch import WebFetchTool
    from agent_eval.maintenance_fixture import build_fixture
    from agent_eval.runner import _build_manager

    from agent import JsonlSessionStore

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("external implementation must not execute")

    monkeypatch.setattr(WebFetchTool, "invoke", forbidden)
    sessions = JsonlSessionStore(tmp_path / "sessions")
    memory_store, memory, broker = build_fixture(tmp_path, sessions, [])
    manager = _build_manager(
        sessions,
        ProviderSpec(model="openai/test", api_key="fake"),
        memory_maintenance=memory,
        approval_broker=broker,
    )
    try:
        assert manager._tool_registry is not None
        tool = manager._tool_registry.get("web_fetch")
        assert tool.parameters_schema == WebFetchTool.parameters_schema
        assert tool.description == WebFetchTool.description
        result = tool.invoke({"url": "https://example.com"})
        assert result.is_error
        assert "outside_isolated_fixture" in result.text
    finally:
        manager.close()
        memory_store.close()


def test_review_judge_rejects_original_gap_and_partial_coverage() -> None:
    contract = next(
        c for c in load_contracts(Path(__file__).resolve().parents[1] / "cases") if c.id == "C29"
    )
    case = contract.cases[0]
    empty = Trace(
        case_id=case.id,
        tool_calls=[],
        final_text="我没有完整库存，请提供导出",
        stop_reason="end_turn",
    )
    assert not judge_case(case, contract, empty).passed

    def trace(complete: bool) -> Trace:
        return Trace(
            case_id=case.id,
            tool_calls=[
                ToolCallTrace(
                    name="skill_read",
                    args={"skill_id": "builtin/maintain-af-memory"},
                    result_text="<name>maintain-af-memory</name>",
                    is_error=False,
                ),
                ToolCallTrace(
                    name="semantic_memory_audit",
                    args={"action": "start"},
                    result_text='{"total":2}',
                    is_error=False,
                ),
                ToolCallTrace(
                    name="semantic_memory_audit",
                    args={"action": "record"},
                    result_text=json.dumps({"coverage_complete": complete}),
                    is_error=False,
                ),
            ],
            final_text="长期读书偏好保留，通知回执建议退役",
            stop_reason="end_turn",
        )

    assert not judge_case(case, contract, trace(False)).passed
    assert judge_case(case, contract, trace(True)).passed
    missing_skill = trace(True)
    missing_skill.tool_calls.pop(0)
    assert not judge_case(case, contract, missing_skill).passed
    failed_skill = trace(True)
    failed_skill.tool_calls[0] = replace(failed_skill.tool_calls[0], is_error=True)
    assert not judge_case(case, contract, failed_skill).passed
