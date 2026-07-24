"""voice_bridge 内部 HTTP 连接的网络策略测试。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from voice_bridge import network


@pytest.mark.parametrize(
    ("url", "expected_trust_env"),
    [
        ("http://127.0.0.1:18800/v1/sessions", False),
        ("http://127.8.9.10:18800/v1/sessions", False),
        ("http://localhost:18800/v1/sessions", False),
        ("http://service.localhost:18800/v1/sessions", False),
        ("http://[::1]:18800/v1/sessions", False),
        ("https://agent-bridge.example.com/v1/sessions", True),
    ],
)
def test_create_http_client_only_bypasses_proxy_for_loopback(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    expected_trust_env: bool,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def fake_async_client(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr("voice_bridge.network.httpx.AsyncClient", fake_async_client)

    network.create_http_client(url, timeout=3.0)

    assert captured_kwargs == {
        "timeout": 3.0,
        "trust_env": expected_trust_env,
    }


@pytest.mark.asyncio
async def test_agent_bridge_client_bypasses_proxy_for_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        async def request(
            self,
            method: str,
            url: str,
            json: dict[str, str] | None = None,
        ) -> httpx.Response:
            captured.update(method=method, url=url, json=json)
            return httpx.Response(200, json={})

        async def aclose(self) -> None:
            captured["closed"] = True

    def fake_async_client(**kwargs: Any) -> FakeAsyncClient:
        captured["client_kwargs"] = kwargs
        return FakeAsyncClient()

    monkeypatch.setattr("voice_bridge.network.httpx.AsyncClient", fake_async_client)

    from voice_bridge.clients import AgentBridgeClient

    await AgentBridgeClient("http://127.0.0.1:18800").switch_channel(
        "session-1",
        "voice",
    )

    assert captured == {
        "client_kwargs": {"timeout": 10.0, "trust_env": False},
        "method": "POST",
        "url": "http://127.0.0.1:18800/v1/sessions/session-1/channel",
        "json": {"channel": "voice"},
        "closed": True,
    }
