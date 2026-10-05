from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest


def load() -> ModuleType:
    path = Path(__file__).parents[1] / "assistant-phase-smoke/run.py"
    spec = importlib.util.spec_from_file_location("assistant_phase_smoke_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_requires_opt_in_and_reserves_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load()
    target = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["smoke", "--output", str(target)])
    monkeypatch.setattr(module, "run", lambda: pytest.fail("unexpected real call"))
    with pytest.raises(SystemExit):
        module.main()
    target.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["smoke", "--allow-llm", "--output", str(target)])
    with pytest.raises(FileExistsError):
        module.main()


def test_wire_limits_cover_model_tokens_endpoint_retry_and_upload() -> None:
    module = load()
    budget = module.WireBudget()
    upload = httpx.Request("POST", "https://api.deepseek.com/files")
    budget.observe(upload)
    with pytest.raises(module.BoundaryExceeded):
        budget.observe(upload)
    request = httpx.Request(
        "POST",
        "https://api.deepseek.com/responses",
        json={
            "model": module.MODEL.split("/", 1)[1],
            "max_output_tokens": 1024,
        },
    )
    for call in (1, 2):
        budget.current_call = call
        budget.observe(request)
        with pytest.raises(module.BoundaryExceeded):
            budget.observe(request)
    budget.current_call = 3
    with pytest.raises(module.BoundaryExceeded):
        budget.observe(request)
    for url in ("https://api.deepseek.com/chat/completions", "https://example.com/responses"):
        with pytest.raises(module.BoundaryExceeded):
            module.WireBudget().observe(httpx.Request("POST", url))
    fresh = module.WireBudget()
    fresh.current_call = 1
    with pytest.raises(module.BoundaryExceeded):
        fresh.observe(
            httpx.Request(
                "POST", str(request.url), json={"model": "wrong", "max_output_tokens": 9999}
            )
        )


@pytest.mark.parametrize("protocol_failure", [False, True])
def test_full_agent_smoke_with_only_fake_http(
    monkeypatch: pytest.MonkeyPatch, protocol_failure: bool
) -> None:
    module = load()
    monkeypatch.setattr(
        module,
        "credential_source_for_mode",
        lambda _: SimpleNamespace(get=lambda _: SimpleNamespace(value="fixture-secret")),
    )
    requests: list[dict[str, Any]] = []

    def files(_transport: Any, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/files"
        return httpx.Response(
            200,
            json={
                "id": "file-fixture",
                "object": "file",
                "bytes": 500,
                "created_at": 1,
                "filename": "synthetic.png",
                "purpose": "user_data",
                "expires_at": 9999999999,
            },
        )

    async def responses(_transport: Any, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        items: list[dict[str, Any]] = [
            {
                "id": f"m{len(requests)}",
                "type": "message",
                "role": "assistant",
                "phase": "commentary" if first else "final_answer",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "checking" if first else "cobalt-115",
                        "annotations": [],
                    }
                ],
            }
        ]
        if first:
            items.append(
                {
                    "id": "fc",
                    "type": "function_call",
                    "call_id": "call-fixture",
                    "name": "lookup_fixture",
                    "arguments": '{"code":"AF-115"}',
                    "status": "completed",
                }
            )
        raw = {
            "type": "response.completed",
            "response": {
                "id": f"r{len(requests)}",
                "object": "response",
                "created_at": 1,
                "model": body["model"],
                "status": "completed",
                "output": items,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "input_tokens_details": {"cached_tokens": 20},
                },
            },
        }
        if protocol_failure:
            raw["response"]["status"] = "in_progress"
        return httpx.Response(
            200,
            content=f"event: {raw['type']}\ndata: {json.dumps(raw)}\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", files)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", responses)
    result = module.run()
    if protocol_failure:
        assert not result["passed"]
        assert result["error"] == "LLMProviderError"
        assert result["error_chain"][0]["message"].startswith("responses_protocol_error:")
        assert result["stream_shapes"][0]["type"] == "response.completed"
        assert len(result["wire_requests"]) == 2
        return
    assert result["passed"], result
    assert len(result["wire_requests"]) == 3
    assert result["phases"] == ["commentary", "final_answer"]
    assert result["replayed_phases"] == result["phases"]
    assert any(item.get("phase") == "commentary" for item in requests[1]["input"])
    assert result["calls"][1]["usage"]["cached_prompt_tokens"] == 20
    assert "fixture-secret" not in json.dumps(result)


def test_diagnostic_shapes_do_not_include_text_reasoning_or_raw_errors() -> None:
    module = load()
    shape = module.event_shape(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "phase": "commentary",
                "status": "completed",
                "content": [{"type": "output_text", "text": "private body"}],
            },
        }
    )
    assert shape["item"]["phase"] == "commentary"
    assert "private body" not in json.dumps(shape)
    assert (
        module.event_shape(
            {
                "type": "response.output_item.done",
                "item": {"type": "message", "phase": {}, "content": None},
            }
        )["item"]["phase"]
        == "unknown"
    )
    error = ValueError("raw exception contains private input")
    error.body = {"error": {"message": "bad field fixture-key", "code": "invalid_request"}}  # type: ignore[attr-defined]
    chain = module.safe_error_chain(error, "fixture-key")
    assert chain[0]["provider_message"] == "bad field [REDACTED]"
    assert "private input" not in json.dumps(chain)
