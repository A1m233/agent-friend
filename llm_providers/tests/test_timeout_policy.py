from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from llm_providers.cancellable_stream import StreamIdleTimeoutExceeded, open_cancellable_stream
from llm_providers.error_normalization import map_provider_error
from llm_providers.timeouts import (
    ModelCallDeadlineExceeded,
    ModelTimeoutPolicy,
    resolve_timeout_policy,
)

from llm_providers import (
    LLMClient,
    ModelCallControl,
    ModelRequest,
    ProviderSpec,
    build_provider_spec,
)


def test_catalog_default_does_not_clip_interactive_wait_but_explicit_value_does() -> None:
    for provider in ("deepseek", "kimi"):
        spec = build_provider_spec(provider_id=provider, api_key="fixture")
        policy = resolve_timeout_policy(
            spec.defaults, {}, streaming=True, provider_timeout_is_default=spec.timeout_is_default
        )
        assert policy.response_seconds == policy.idle_seconds == 300
        completion = resolve_timeout_policy(
            spec.defaults, {}, streaming=False, provider_timeout_is_default=spec.timeout_is_default
        )
        assert completion.response_seconds == 90
        explicit = resolve_timeout_policy(
            spec.defaults,
            {"timeout": 25},
            streaming=True,
            provider_timeout_is_default=spec.timeout_is_default,
        )
        assert explicit.response_seconds == 25
        assert explicit.source == "request_override"


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_policy_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        ModelTimeoutPolicy(response_seconds=value)


def test_explicit_provider_timeout_can_extend_default() -> None:
    policy = resolve_timeout_policy({"timeout": 600}, {}, streaming=True)
    assert policy.response_seconds == policy.idle_seconds == 600
    assert policy.http_timeout().connect == 15


class Source:
    request_id: str | None = None

    def __init__(self, opening: float = 0, gap: float = 0) -> None:
        self.opening, self.gap = opening, gap
        self.closed = threading.Event()

    async def open(self) -> AsyncIterator[Any]:
        await asyncio.sleep(self.opening)

        async def chunks() -> AsyncIterator[Any]:
            yield {"type": "response.created"}
            await asyncio.sleep(self.gap)
            yield {"type": "response.completed"}

        return chunks()

    async def close(self) -> None:
        self.closed.set()


def test_response_wait_is_independent_of_idle_and_cleanup_is_complete() -> None:
    source = Source(opening=0.12)
    stream = open_cancellable_stream(
        source,
        lambda: None,
        timeout_policy=ModelTimeoutPolicy(response_seconds=1, idle_seconds=0.05),
    )
    assert len(list(stream)) == 2
    stream.close()
    assert source.closed.wait(1)


@pytest.mark.parametrize("opening,gap,stage", [(1, 0, "awaiting_response"), (0, 1, "stream_idle")])
def test_silence_identifies_stage_and_cancels_underlying_source(
    opening: float,
    gap: float,
    stage: str,
) -> None:
    source = Source(opening, gap)
    with pytest.raises(StreamIdleTimeoutExceeded) as caught:
        stream = open_cancellable_stream(
            source,
            lambda: None,
            timeout_policy=ModelTimeoutPolicy(response_seconds=0.1, idle_seconds=0.1),
        )
        list(stream)
    evidence = map_provider_error(caught.value).error_evidence
    assert evidence is not None and evidence.timeout_stage == stage
    assert evidence.timeout_seconds == 0.1
    assert source.closed.wait(1)


def test_activity_can_continue_beyond_fifteen_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(
        "llm_providers.cancellable_stream.time", SimpleNamespace(monotonic=lambda: clock[0])
    )

    class ActiveSource(Source):
        async def open(self) -> AsyncIterator[Any]:
            async def chunks() -> AsyncIterator[Any]:
                for _ in range(8):
                    clock[0] += 200
                    yield {"type": "response.reasoning_text.delta"}

            return chunks()

    source = ActiveSource()
    stream = open_cancellable_stream(source, lambda: None, timeout_policy=ModelTimeoutPolicy())
    assert len(list(stream)) == 8
    assert clock[0] == 1600
    stream.close()
    assert source.closed.wait(1)


def test_first_response_after_old_45_seconds_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(
        "llm_providers.cancellable_stream.time", SimpleNamespace(monotonic=lambda: clock[0])
    )

    class SlowSource(Source):
        async def open(self) -> AsyncIterator[Any]:
            clock[0] = 50.0
            await asyncio.sleep(0.1)  # Give the host clock a chance to reject the old 45s limit.
            return await super().open()

    stream = open_cancellable_stream(
        SlowSource(), lambda: None, timeout_policy=ModelTimeoutPolicy()
    )
    assert len(list(stream)) == 2
    stream.close()


@pytest.mark.parametrize(
    "kind,stage",
    [
        (httpx.ConnectTimeout, "connect"),
        (httpx.WriteTimeout, "write"),
        (httpx.PoolTimeout, "pool"),
        (httpx.ReadTimeout, "unknown"),
    ],
)
def test_sdk_timeout_preserves_only_observable_stage(kind: type[Exception], stage: str) -> None:
    import openai

    outer = openai.APITimeoutError(request=httpx.Request("POST", "https://fixture.invalid"))
    outer.__cause__ = kind("private provider content")
    error = map_provider_error(outer)
    assert error.error_evidence is not None
    assert error.error_evidence.timeout_stage == stage
    assert "private" not in str(error.error_evidence)


def test_cleanup_failure_is_logged_separately_without_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingClose(Source):
        async def close(self) -> None:
            raise RuntimeError("private cleanup content")

    stream = open_cancellable_stream(
        FailingClose(), lambda: None, timeout_policy=ModelTimeoutPolicy()
    )
    assert len(list(stream)) == 2
    stream.close()
    assert "status=failed" in caplog.text
    assert "private cleanup content" not in caplog.text


def test_cleanup_duration_is_not_stream_idle() -> None:
    class SlowClose(Source):
        async def close(self) -> None:
            await asyncio.sleep(0.2)
            self.closed.set()

    source = SlowClose()
    stream = open_cancellable_stream(
        source, lambda: None, timeout_policy=ModelTimeoutPolicy(idle_seconds=0.05)
    )
    assert len(list(stream)) == 2
    stream.close()
    assert source.closed.is_set()
    assert not stream._thread.is_alive()


@pytest.fixture
def http_server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            if self.path.startswith("/slow"):
                time.sleep(1.2 if self.path.startswith("/slow2") else 0.15)
            if self.path.startswith("/retry"):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path.endswith("/responses"):
                response = {
                    "id": "resp-test",
                    "model": "fixture",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg-1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                        }
                    ],
                }
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if body.get("stream") else "application/json",
                )
                self.end_headers()
                data = (
                    (
                        "data: "
                        + json.dumps({"type": "response.completed", "response": response})
                        + "\n\n"
                    )
                    if body.get("stream")
                    else json.dumps(response)
                )
                self.wfile.write(data.encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "id": "test",
                "model": "test",
                "object": "chat.completion.chunk",
                "created": 0,
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            try:
                if self.path.startswith("/active"):
                    for _ in range(8):
                        self.wfile.write(
                            b'data: {"id":"test","object":"chat.completion.chunk","created":0,"model":"test","choices":[{"index":0,"delta":{"content":"a"},"finish_reason":null}]}\n\n'
                        )
                        self.wfile.flush()
                        time.sleep(0.3)
                if self.path.startswith("/idle"):
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(1)
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("model", ["openai/test", "openai/kimi-k2.6"])
def test_real_sdk_waits_for_headers_without_idle_cutoff(
    http_server: tuple[str, list[dict[str, Any]]],
    model: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="llm_providers.call_observation")
    url, seen = http_server
    client = LLMClient(
        ProviderSpec(model=model, api_key="fixture", api_base=url + "/slow"),
        network_retries=0,
        engine_overload_retries=0,
    )
    events = list(
        client.stream(
            ModelRequest(
                messages=[{"role": "user", "content": "test"}],
                control=ModelCallControl(
                    timeout_policy=ModelTimeoutPolicy(response_seconds=2, idle_seconds=0.1)
                ),
            )
        )
    )
    assert events and len(seen) == 1
    assert "timeout" not in seen[0] and "deadline_monotonic" not in seen[0]
    observation = next(
        json.loads(record.message.split("model_call_observation ", 1)[1])
        for record in caplog.records
        if "model_call_observation " in record.message
    )
    assert observation["timeout_policy"]["response_seconds"] == 2
    assert observation["timeout_policy"]["idle_seconds"] == 0.1
    assert observation["business_deadline"] is False


def test_business_deadline_stops_retry_backoff_without_second_request(
    http_server: tuple[str, list[dict[str, Any]]],
) -> None:
    url, seen = http_server
    client = LLMClient(
        ProviderSpec(model="openai/test", api_key="fixture", api_base=url + "/retry"),
        network_retry_backoff_seconds=5,
    )
    with pytest.raises(ModelCallDeadlineExceeded):
        list(
            client.stream(
                ModelRequest(
                    messages=[{"role": "user", "content": "test"}],
                    control=ModelCallControl(deadline_monotonic=time.monotonic() + 1),
                )
            )
        )
    assert len(seen) == 1


def test_real_sdk_has_no_total_stream_duration_timeout(
    http_server: tuple[str, list[dict[str, Any]]],
) -> None:
    url, seen = http_server
    client = LLMClient(
        ProviderSpec(model="openai/test", api_key="fixture", api_base=url + "/active"),
        network_retries=0,
    )
    started = time.monotonic()
    assert list(
        client.stream(
            ModelRequest(
                messages=[{"role": "user", "content": "test"}],
                control=ModelCallControl(
                    timeout_policy=ModelTimeoutPolicy(response_seconds=2, idle_seconds=0.5)
                ),
            )
        )
    )
    assert time.monotonic() - started > 2
    assert len(seen) == 1


@pytest.mark.parametrize("streaming", [True, False])
def test_real_responses_sdk_uses_policy_for_both_entrypoints(
    http_server: tuple[str, list[dict[str, Any]]], streaming: bool
) -> None:
    url, seen = http_server
    spec = replace(
        build_provider_spec(provider_id="deepseek", api_key="fixture"), api_base=url + "/slow2"
    )
    client = LLMClient(spec, network_retries=0, engine_overload_retries=0)
    request = ModelRequest(
        messages=[{"role": "user", "content": "test"}],
        control=ModelCallControl(
            timeout_policy=ModelTimeoutPolicy(response_seconds=3, idle_seconds=1)
        ),
    )
    if streaming:
        assert list(client.stream(request))
    else:
        assert client.generate(request).text == "ok"
    assert len(seen) == 1
    assert "timeout" not in seen[0]


def test_real_sdk_comment_heartbeat_does_not_hide_event_silence(
    http_server: tuple[str, list[dict[str, Any]]],
) -> None:
    from llm_providers import LLMStreamTimeoutError

    url, seen = http_server
    client = LLMClient(
        ProviderSpec(model="openai/test", api_key="fixture", api_base=url + "/idle"),
        network_retries=0,
    )
    with pytest.raises(LLMStreamTimeoutError) as caught:
        list(
            client.stream(
                ModelRequest(
                    messages=[{"role": "user", "content": "test"}],
                    control=ModelCallControl(
                        timeout_policy=ModelTimeoutPolicy(response_seconds=2, idle_seconds=0.15)
                    ),
                )
            )
        )
    assert caught.value.error_evidence is not None
    assert caught.value.error_evidence.timeout_stage == "stream_idle"
    assert len(seen) == 1
