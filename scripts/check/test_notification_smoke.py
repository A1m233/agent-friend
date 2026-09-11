from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest

from llm_providers import (
    LLMClient,
    LLMTurnDone,
    ModelCallControl,
    ModelRequest,
    build_provider_spec,
)


def load() -> ModuleType:
    path = Path(__file__).parents[1] / "notification-smoke/run.py"
    spec = importlib.util.spec_from_file_location("notification_smoke_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_rejects_model_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = load()
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["smoke", "--output", str(output)])
    monkeypatch.setattr(module, "run", lambda *_args, **_kwargs: pytest.fail("real invocation"))
    with pytest.raises(SystemExit):
        module.main()
    assert not output.exists()
    assert not output.with_suffix(".inputs").exists()


@pytest.mark.parametrize("marker", ["directory", "worktree-file"])
def test_output_rejects_git_checkout_and_relative_paths(tmp_path: Path, marker: str) -> None:
    module = load()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if marker == "directory":
        (checkout / ".git").mkdir()
    else:
        (checkout / ".git").write_text("gitdir: somewhere", encoding="utf-8")
    with pytest.raises(ValueError, match="Git checkout"):
        module.private_output(checkout / "private" / "report.json")
    with pytest.raises(ValueError, match="absolute"):
        module.private_output(Path("private/report.json"))


def test_capture_keeps_model_and_final_wire_but_omits_headers_and_redacts_credentials(
    tmp_path: Path,
) -> None:
    module = load()
    evidence = module.InputEvidence(tmp_path / "report.json")
    evidence.secret = "fixture-api-key-never-persist"
    budget = module.WireBudget(evidence)
    record: dict[str, Any] = {"caseIndex": 1, "logicalCalls": 0, "wireRequests": 0}
    budget.current = record
    budget.endpoint = ("api.deepseek.com", "/responses")
    original = "人格正文原样保留\n背景记忆原样保留\n当前事件：照片分类在等待回答。"

    def stream(request: ModelRequest) -> Any:
        body = {
            "model": "fixture-model",
            "input": [
                {"role": item["role"], "content": [{"type": "input_text", "text": item["content"]}]}
                for item in request.messages
            ],
            "api_key": evidence.secret,
        }
        budget.observe(
            httpx.Request(
                "POST",
                "https://api.deepseek.com/responses",
                json=body,
                headers={"Authorization": "Bearer " + evidence.secret, "x-private": "header-only"},
            )
        )
        # Evidence must survive failures that occur after the final request was constructed.
        raise TimeoutError("fixture timeout")
        yield LLMTurnDone("end_turn")  # pragma: no cover

    model = module.ObservedModel(SimpleNamespace(stream=stream), record, budget)
    request = ModelRequest(
        messages=[{"role": "system", "content": original}],
        settings={
            "timeout": 4.0,
            "apiKey": evidence.secret,
            "extra_headers": {"Authorization": "Bearer " + evidence.secret},
            "fixture": "embedded " + evidence.secret,
        },
        control=ModelCallControl(allow_retries=False),
    )
    with pytest.raises(TimeoutError, match="fixture"):
        list(model.stream(request))
    assert record["logicalCalls"] == record["wireRequests"] == budget.total == 1
    model_path = tmp_path / record["inputEvidence"]["model-request"]["file"]
    wire_path = tmp_path / record["inputEvidence"]["wire"]["file"]
    model_body = json.loads(model_path.read_text(encoding="utf-8"))
    wire_body = json.loads(wire_path.read_text(encoding="utf-8"))
    assert model_body["messages"][0]["content"] == original
    assert wire_body["input"][0]["content"][0]["text"] == original
    assert model_body["settings"]["apiKey"] == "[REDACTED]"
    assert wire_body["api_key"] == "[REDACTED]"
    for path, stage in ((model_path, "model-request"), (wire_path, "wire")):
        content = path.read_bytes()
        assert evidence.secret.encode() not in content
        assert b"header-only" not in content
        assert record["inputEvidence"][stage]["sha256"] == hashlib.sha256(content).hexdigest()
    assert original not in json.dumps(record, ensure_ascii=False)
    assert record["inputEvidence"]["model-request"]["redactedFields"] == 2
    assert record["inputEvidence"]["model-request"]["redactedValues"] == 1
    with pytest.raises(module.SmokeBoundaryExceeded):
        budget.observe(httpx.Request("POST", "https://api.deepseek.com/responses", json={}))
    assert budget.total == 1


def test_evidence_refuses_existing_directory_and_artifact(tmp_path: Path) -> None:
    module = load()
    output = tmp_path / "report.json"
    evidence = module.InputEvidence(output)
    record = {"caseIndex": 1}
    evidence.write(record, "wire", {"fixture": "original"})
    with pytest.raises(FileExistsError):
        evidence.write(record, "wire", {"fixture": "replacement"})
    with pytest.raises(FileExistsError):
        module.InputEvidence(output)
    assert "original" in (evidence.directory / "01.wire.json").read_text(encoding="utf-8")


def test_wire_evidence_failure_prevents_send_budget(tmp_path: Path) -> None:
    module = load()
    evidence = module.InputEvidence(tmp_path / "report.json")
    record = {"caseIndex": 1, "wireRequests": 0}
    evidence.write(record, "wire", {})
    budget = module.WireBudget(evidence)
    budget.current = record
    budget.endpoint = ("api.deepseek.com", "/responses")
    with pytest.raises(FileExistsError):
        budget.observe(httpx.Request("POST", "https://api.deepseek.com/responses", json={}))
    assert record["wireRequests"] == budget.total == 0


def test_capture_observes_actual_sdk_serialized_body_without_http_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load()
    evidence = module.InputEvidence(tmp_path / "report.json")
    evidence.secret = "fixture-sdk-secret"
    budget = module.WireBudget(evidence)
    record: dict[str, Any] = {"caseIndex": 1, "logicalCalls": 0, "wireRequests": 0}
    budget.current = record
    budget.endpoint = ("api.deepseek.com", "/responses")
    sent_bodies: list[dict[str, Any]] = []

    async def fake_send(
        transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer " + evidence.secret
        budget.observe(request)
        sent_bodies.append(json.loads(request.content))
        event = {
            "type": "response.completed",
            "response": {
                "id": "fixture-response",
                "model": "fixture-model",
                "status": "completed",
                "output": [
                    {
                        "id": "fixture-message",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "已收到。"}],
                    }
                ],
            },
        }
        data = f"event: response.completed\ndata: {json.dumps(event)}\n\n"
        return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    model = module.ObservedModel(
        LLMClient(
            build_provider_spec(
                provider_id="deepseek",
                model_id="deepseek/deepseek-flash",
                reasoning_preset="off",
                api_key=evidence.secret,
            )
        ),
        record,
        budget,
    )
    events = list(
        model.stream(
            ModelRequest(
                messages=[{"role": "user", "content": "fixture-context"}],
                settings={"timeout": 2.0, "max_tokens": 512},
                control=ModelCallControl(allow_retries=False),
            )
        )
    )
    assert any(isinstance(event, LLMTurnDone) for event in events)
    artifact = (evidence.directory / "01.wire.json").read_text(encoding="utf-8")
    captured = json.loads(artifact)
    assert captured == sent_bodies[0]
    assert captured["input"][0]["content"][0]["text"] == "fixture-context"
    assert captured["reasoning"] == {"effort": "none"}
    assert "messages" not in captured and "max_tokens" not in captured
    assert captured["max_output_tokens"] == 512
    assert "authorization" not in artifact and evidence.secret not in artifact
    assert record["wireRequests"] == budget.total == 1
