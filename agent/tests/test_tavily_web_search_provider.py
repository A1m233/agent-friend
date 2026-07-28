"""Tavily web-search adapter tests; all HTTP behavior is faked locally."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from agent.tools.builtin.web_search import (
    WebSearchProviderError,
    WebSearchRateLimitError,
)
from agent.tools.builtin.web_search.providers.tavily import TavilyProvider
from tavily import TavilyClient
from tavily.errors import UsageLimitExceededError


def _response(status_code: int, **headers: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers)
    response.url = "https://api.tavily.com/search"
    response.request = requests.Request("POST", response.url).prepare()
    return response


def _run_response_hooks(client: TavilyClient, response: requests.Response) -> None:
    for hook in client.session.hooks["response"]:
        hook(response)


def test_success_forwards_timeout_and_preserves_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        response = _response(200, **{"x-request-id": "header-request"})
        _run_response_hooks(client, response)
        return {
            "request_id": "body-request",
            "response_time": 0.25,
            "results": [{"title": "A", "url": "https://example.test/", "content": "snippet"}],
        }

    monkeypatch.setattr(TavilyClient, "search", fake_search)

    result = TavilyProvider("test-key").search("query", 5, timeout_seconds=7.5)

    assert captured == {"query": "query", "max_results": 5, "timeout": 7.5}
    assert result.request_id == "body-request"
    assert result.provider_duration_seconds == 0.25
    assert result.hits[0].title == "A"


def test_rate_limit_preserves_retry_after_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_search(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        response = _response(
            429,
            **{"retry-after": "2.5", "x-request-id": "rate-request"},
        )
        _run_response_hooks(client, response)
        raise UsageLimitExceededError("too many requests")

    monkeypatch.setattr(TavilyClient, "search", fake_search)

    with pytest.raises(WebSearchRateLimitError) as caught:
        TavilyProvider("test-key").search("query", 5, timeout_seconds=7.5)

    assert caught.value.retry_after_seconds == 2.5
    assert caught.value.request_id == "rate-request"
    assert caught.value.is_retryable is True


def test_http_503_is_structured_as_retryable_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_search(client: TavilyClient, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        response = _response(503, **{"x-request-id": "unavailable-request"})
        _run_response_hooks(client, response)
        response.raise_for_status()
        raise AssertionError("unreachable")

    monkeypatch.setattr(TavilyClient, "search", fake_search)

    with pytest.raises(WebSearchProviderError) as caught:
        TavilyProvider("test-key").search("query", 5, timeout_seconds=7.5)

    assert caught.value.status_code == 503
    assert caught.value.request_id == "unavailable-request"
    assert caught.value.is_retryable is True
