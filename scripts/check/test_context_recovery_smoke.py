"""The long-session observation runner is exercised with no real provider calls."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from llm_providers import (
    LanguageModelCapabilities,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ModelRuntimeConfig,
    ProviderAssistantTurn,
    ProviderToolCall,
)


def load(name: str = "long_session") -> ModuleType:
    path = Path(__file__).parents[1] / "context-recovery-smoke" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"context_recovery_smoke_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeModel:
    context_window = 100_000
    capabilities = LanguageModelCapabilities(
        input_usage_scope="request",
        context_checkpoint_mode="replace_complete_groups",
    )

    def __init__(self, expected: dict[str, Any]) -> None:
        self.expected = expected
        self.requests: list[ModelRequest] = []
        self.main_requests = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse("fixture")

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.requests.append(request)
        assert request.control.retry_limit == 0
        assert not request.control.allow_retries and not request.control.allow_output_retries
        if (
            request.control.trace_context
            and request.control.trace_context.operation == "context_compaction"
        ):
            yield LLMTurnDone(
                stop_reason="end_turn",
                assistant_turn=ProviderAssistantTurn(
                    "<summary>Latest status: " + json.dumps(self.expected) + "</summary>",
                ),
            )
            return
        self.main_requests += 1
        if self.main_requests == 1:
            yield LLMToolCallDelta(
                index=0,
                tool_call_id="fixture-call",
                tool_name="checkpoint_fixture",
                args_json_delta="{}",
            )
            yield LLMTurnDone(
                stop_reason="tool_use",
                assistant_turn=ProviderAssistantTurn(
                    None,
                    (ProviderToolCall(0, "fixture-call", "checkpoint_fixture", "{}"),),
                ),
            )
            return
        text = json.dumps(self.expected)
        yield LLMTextDelta(text)
        yield LLMTurnDone(stop_reason="end_turn", assistant_turn=ProviderAssistantTurn(text))


def test_three_scenarios_use_isolated_sessions_and_six_fake_requests(tmp_path: Path) -> None:
    module = load()
    model = FakeModel(module.EXPECTED)
    output = tmp_path / "new-report.json"
    report = module.run_long_session(
        model,
        config=ModelRuntimeConfig("deepseek/test", "none"),
        output=output,
        connection_name="fixture",
        model_label="fixture",
        real_provider=False,
    )
    assert report["status"] == "passed", report
    assert report["request_count"] == 6
    assert len(model.requests) == 6
    assert not report["real_provider_observation"]
    assert report["no_repeated_in_memory_actions"]
    assert [row["scenario"] for row in report["scenarios"]] == [
        "task_compaction",
        "explicit_recovery",
        "cold_recovery",
    ]
    assert all(all(row["state_checks"].values()) for row in report["scenarios"])
    assert report["scenarios"][0]["compactions"][-1]["version"] == 4
    summary_requests = [
        request
        for request in model.requests
        if request.control.trace_context
        and request.control.trace_context.operation == "context_compaction"
    ]
    first_source = str(summary_requests[0].messages)
    assert "confirmed success" in first_source
    assert "process still running" in first_source
    assert "result unavailable" in first_source
    assert "not started" in first_source
    assert report["scenarios"][0]["trigger"] == "synthetic_force_at_completed_tool_boundary"
    assert len(report["scenarios"][0]["compactions"]) == 1
    assert sum(row["operation"] == "context_compaction" for row in report["requests"]) == 2
    assert all(row["in_memory_actions"] == 1 for row in report["scenarios"])
    assert report["scenarios"][1]["failure_source"] == "synthetic_pre_provider_fixture"
    assert report["scenarios"][1]["compactions"] == report["scenarios"][2]["compactions"]
    assert json.loads(output.read_text(encoding="utf-8"))["request_count"] == 6
    assert list(tmp_path.iterdir()) == [output]


def test_eight_request_limit_is_shared_across_summary_main_and_cold_wrappers() -> None:
    module = load()
    model = FakeModel(module.EXPECTED)
    report: dict[str, Any] = {"requests": []}
    warm = module.BoundedModel(model, report)
    cold = module.BoundedModel(model, report)
    request = ModelRequest(messages=[{"role": "user", "content": "synthetic"}])
    for index in range(8):
        client = warm if index < 4 else cold
        if index % 2:
            client.generate(request)
        else:
            list(client.stream(request))
    assert len(model.requests) == len(report["requests"]) == 8
    with pytest.raises(module.RequestLimitExceeded):
        list(cold.stream(request))
    assert len(model.requests) == 8
    assert report["request_limit_reached"]
    assert all(request.control.retry_limit == 0 for request in model.requests)


def test_report_paths_are_outside_repository_and_never_overwritten(tmp_path: Path) -> None:
    module = load()
    model = FakeModel(module.EXPECTED)
    kwargs = dict(
        config=ModelRuntimeConfig("deepseek/test", "none"),
        connection_name="fixture",
        model_label="fixture",
        real_provider=False,
    )
    with pytest.raises(ValueError, match="outside_repository"):
        module.run_long_session(model, output=module.ROOT / "blocked-report.json", **kwargs)
    output = tmp_path / "existing.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module.run_long_session(model, output=output, **kwargs)
    assert output.read_text(encoding="utf-8") == "keep"
    assert model.requests == []


def test_entrypoint_without_explicit_allow_never_resolves_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load("run")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--scenario",
            "long-session",
            "--model",
            "fixture",
            "--output",
            str(tmp_path / "report.json"),
        ],
    )
    monkeypatch.setattr(
        module,
        "resolve_bridge_runtime_config",
        lambda _: pytest.fail("credentials read without authorization flag"),
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
