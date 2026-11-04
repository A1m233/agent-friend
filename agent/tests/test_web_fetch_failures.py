from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

import agent.tools.builtin.web_fetch.html as html_module
import agent.tools.builtin.web_fetch.pdf as pdf_module
import httpx
import pytest
from agent.tools.builtin.web_fetch import WebFetchTool
from agent.tools.builtin.web_fetch.provider import (
    ExtractedContent,
    WebContentEmptyError,
    WebContentHttpError,
    WebContentTimeoutError,
)
from agent.tools.builtin.web_fetch.tavily_extract import TavilyExtractProvider


def _transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    client = httpx.Client
    monkeypatch.setattr(html_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(pdf_module, "validate_public_url", lambda url: url)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )


@pytest.mark.parametrize("suffix", ["/page", "/paper.pdf"])
@pytest.mark.parametrize(
    ("status", "kind", "hint"),
    [
        (401, "authentication_required", "身份认证"),
        (403, "access_denied", "拒绝访问"),
        (404, "not_found", "页面不存在"),
        (410, "gone", "资源已移除"),
        (408, "timeout", "等待时限"),
        (429, "rate_limit", "限流"),
        (503, "server_error", "服务端"),
        (418, "http_error", "HTTP 响应"),
    ],
)
def test_target_http_errors_reach_model_with_status_and_recovery(
    monkeypatch: pytest.MonkeyPatch, suffix: str, status: int, kind: str, hint: str
) -> None:
    _transport(monkeypatch, lambda request: httpx.Response(status, request=request))
    result = WebFetchTool(html_reader=html_module.SafeHtmlReader()).invoke(
        {"url": f"https://example.org{suffix}?token=private-fixture"}
    )
    assert result.is_error
    assert result.meta["error_kind"] == kind
    assert f"[{kind}]" in result.text
    assert f"HTTP {status}" in result.text
    assert hint in result.text
    assert "private-fixture" not in str(result)
    assert result.meta["fetch_failures"] == [
        {
            "stage": "local_pdf" if suffix.endswith(".pdf") else "local_html",
            "error_kind": kind,
            "http_status": status,
        }
    ]


@pytest.mark.parametrize("suffix", ["/page", "/paper.pdf"])
@pytest.mark.parametrize(
    ("exception", "kind"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network")],
)
def test_transport_failure_is_not_a_site_http_response(
    monkeypatch: pytest.MonkeyPatch, suffix: str, exception: type[httpx.RequestError], kind: str
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exception("secret-bearing raw exception", request=request)

    _transport(monkeypatch, fail)
    result = WebFetchTool(html_reader=html_module.SafeHtmlReader()).invoke(
        {"url": f"https://example.org{suffix}"}
    )
    assert result.meta["error_kind"] == kind
    assert "HTTP " not in result.text
    assert "secret-bearing" not in str(result)


class _MissingPage:
    name = "local_static_html"

    def open(self, url: str, *, focus: str | None = None) -> ExtractedContent:
        raise WebContentHttpError(404)


class _Provider:
    name: ClassVar[str] = "fixture_extract"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def extract(self, url: str, *, focus: str | None = None) -> ExtractedContent:
        if self.error is not None:
            raise self.error
        return ExtractedContent(url, url, "Recovered document")


def test_remote_timeout_does_not_erase_local_404() -> None:
    result = WebFetchTool(
        _Provider(WebContentTimeoutError("private provider detail")),
        html_reader=_MissingPage(),  # type: ignore[arg-type]
    ).invoke({"url": "https://example.org/missing"})
    assert result.is_error
    assert result.meta["error_kind"] == "timeout"
    assert result.meta["fetch_failures"] == [
        {"stage": "local_html", "error_kind": "not_found", "http_status": 404},
        {"stage": "remote_extract", "error_kind": "timeout"},
    ]
    assert "本地网页读取 [not_found] HTTP 404" in result.text
    assert "远端正文服务 [timeout]" in result.text
    assert "private provider detail" not in str(result)


def test_all_three_failed_paths_keep_distinct_causes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_pdf(*args: Any, **kwargs: Any) -> Any:
        raise WebContentHttpError(403)

    monkeypatch.setattr("agent.tools.builtin.web_fetch.tool.download_and_extract_pdf", fail_pdf)
    result = WebFetchTool(
        _Provider(WebContentEmptyError("raw provider detail")),
        html_reader=_MissingPage(),  # type: ignore[arg-type]
    ).invoke({"url": "https://example.org/document"})
    assert result.meta["error_kind"] == "not_found"
    assert [item["error_kind"] for item in result.meta["fetch_failures"]] == [
        "not_found",
        "empty_content",
        "access_denied",
    ]
    assert "本地 PDF 读取 [access_denied] HTTP 403" in result.text
    assert "远端正文服务 [empty_content]" in result.text


def test_successful_fallback_remains_success_and_keeps_diagnostic_cause() -> None:
    result = WebFetchTool(_Provider(), html_reader=_MissingPage()).invoke(  # type: ignore[arg-type]
        {"url": "https://example.org/document"}
    )
    assert not result.is_error
    assert "Recovered document" in result.text
    assert "error_kind" not in result.meta
    assert result.meta["fetch_failures"][0]["http_status"] == 404


@pytest.mark.parametrize(
    ("error_name", "kind"),
    [
        ("TimeoutError", "timeout"),
        ("InvalidAPIKeyError", "auth"),
        ("ForbiddenError", "provider_access_denied"),
        ("UsageLimitExceededError", "rate_limit"),
    ],
)
def test_provider_errors_are_not_reported_as_target_site_errors(
    monkeypatch: pytest.MonkeyPatch, error_name: str, kind: str
) -> None:
    import tavily
    import tavily.errors

    def fail_extract(self: Any, **kwargs: Any) -> Any:
        raise getattr(tavily.errors, error_name)(30 if error_name == "TimeoutError" else "secret")

    monkeypatch.setattr(tavily.TavilyClient, "extract", fail_extract)
    result = WebFetchTool(TavilyExtractProvider("fake-key")).invoke(
        {"url": "https://example.org/page"}
    )
    assert result.is_error
    assert result.meta["error_kind"] == kind
    assert f"远端正文服务 [{kind}]" in result.text
    assert "HTTP 403" not in result.text
    assert "secret" not in str(result)
