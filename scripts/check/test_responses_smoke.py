from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpcore
import httpx
import pytest
from llm_providers.stream_events import ProviderAssistantTurn, ProviderToolCall

from llm_providers import LLMTextDelta, LLMTurnDone


def load() -> ModuleType:
    path = Path(__file__).parents[1] / "responses-smoke/run.py"
    spec = importlib.util.spec_from_file_location("responses_smoke_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_default_rejects_real_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = load()
    target = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["smoke", "--output", str(target)])
    monkeypatch.setattr(module, "run_provider", lambda *_: pytest.fail("real invocation"))
    with pytest.raises(SystemExit):
        module.main()
    assert not target.exists()


def test_wire_budget_blocks_retries_and_wrong_endpoints() -> None:
    module = load()
    budget = module.WireBudget("api.deepseek.com", "/responses")
    request = httpx.Request("POST", "https://api.deepseek.com/responses", content=b"{}")
    budget.observe(request)
    with pytest.raises(module.SmokeBoundaryExceeded):
        budget.observe(request)
    budget.call_requests = 0
    with pytest.raises(module.SmokeBoundaryExceeded):
        budget.observe(httpx.Request("POST", "https://api.deepseek.com/chat/completions"))
    budget.observe(request)
    budget.call_requests = 0
    with pytest.raises(module.SmokeBoundaryExceeded):
        budget.observe(request)
    assert len(budget.requests) == 2


def test_offline_guard_blocks_target_origin_for_direct_and_proxy_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(__file__).parents[1] / "test/no_external_http.py"
    spec = importlib.util.spec_from_file_location("offline_http_guard_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.block_external_http.__wrapped__(monkeypatch)
    origin = httpcore.Origin(b"https", b"api.deepseek.com", 443)
    for pool in (
        httpcore.ConnectionPool,
        httpcore.AsyncConnectionPool,
        httpcore.HTTPProxy,
        httpcore.AsyncHTTPProxy,
    ):
        with pytest.raises(pytest.fail.Exception, match="external HTTP blocked"):
            pool.create_connection(None, origin)


def test_smoke_consumes_public_tool_turn_without_persisting_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load()
    received = []

    class Model:
        def stream(self, request: Any) -> Any:
            received.append(request)
            if len(received) == 1:
                yield LLMTurnDone(
                    stop_reason="tool_use",
                    assistant_turn=ProviderAssistantTurn(
                        None,
                        (ProviderToolCall(0, "call1", "lookup_fixture", '{"record":"AF-114"}'),),
                    ),
                )
            else:
                yield LLMTextDelta("cobalt-73")
                yield LLMTurnDone(
                    stop_reason="end_turn", assistant_turn=ProviderAssistantTurn("cobalt-73")
                )

    monkeypatch.setattr(module, "create_language_model", lambda **_: Model())
    monkeypatch.setattr(
        module,
        "credential_source_for_mode",
        lambda _: SimpleNamespace(get=lambda _: SimpleNamespace(value="fixture-secret")),
    )
    result = module.run_provider("kimi")
    assert result["passed"] and len(received) == 2
    assert received[1].messages[-1]["tool_call_id"] == "call1"
    assert "cobalt-73" not in str(result) and "fixture-secret" not in str(result)


def test_smoke_failure_keeps_redacted_provider_message() -> None:
    module = load()
    error = ValueError("do not retain raw exception")
    error.body = {"error": {"code": "bad_request", "message": "invalid field fixture-key"}}  # type: ignore[attr-defined]
    result = module.safe_error(error, "fixture-key")
    assert result["provider_message"] == "invalid field [REDACTED]"
    assert "do not retain raw exception" not in str(result)


def test_loopback_budget_requires_the_exact_cpa_port() -> None:
    module = load()
    budget = module.WireBudget("127.0.0.1", "/v1/responses", "http", 8317)
    with pytest.raises(module.SmokeBoundaryExceeded):
        budget.observe(httpx.Request("POST", "http://127.0.0.1:8318/v1/responses"))
    budget.observe(httpx.Request("POST", "http://127.0.0.1:8317/v1/responses", content=b"{}"))
    assert budget.requests[0]["port"] == 8317
    assert budget.requests[0]["scheme"] == "http"


@pytest.mark.parametrize("status", [400, 503])
def test_cpa_gateway_failure_skips_remaining_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    path = Path(__file__).parents[1] / "model-connections-smoke/cpa.py"
    spec = importlib.util.spec_from_file_location("cpa_smoke_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "config.yaml"
    config.write_text("api-keys: [fixture-secret]", encoding="utf-8")
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys, "argv", ["cpa", "--allow-llm", "--config", str(config), "--output", str(output)]
    )
    calls = []

    def run_provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args)
        return {"passed": False, "error": {"status_code": status}}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def get(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"data": [{"id": "gpt-5.6-luna"}]}
            )

    monkeypatch.setattr(module.httpx, "Client", Client)
    monkeypatch.setattr(
        module,
        "load",
        lambda name, path: SimpleNamespace(source_identity=lambda: {}, run_provider=run_provider),
    )
    assert module.main() == 1
    assert len(calls) == 1
    assert "fixture-secret" not in output.read_text(encoding="utf-8")
