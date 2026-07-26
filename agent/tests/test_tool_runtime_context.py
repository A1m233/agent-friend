from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.tool_runtime import ToolRunContext, UrlAuthorizationLedger


def test_user_urls_are_normalized_and_scoped_to_one_context() -> None:
    context = ToolRunContext.create(
        "请读取 HTTPS://Example.COM:443/path?q=1#part。",
        now=datetime(2026, 7, 26, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    authorization = context.url_authorizations.authorize("https://example.com/path?q=1")
    assert authorization is not None
    assert authorization.source == "user_input"
    assert context.trusted_timezone == "Asia/Shanghai"


def test_search_results_must_be_registered_by_successful_tool_result() -> None:
    context = ToolRunContext.create("no urls")
    assert context.url_authorizations.authorize("https://example.com/result") is None
    context.register_tool_result("web_search", "call-1", ["https://example.com/result"])
    authorization = context.url_authorizations.authorize("https://example.com/result")
    assert authorization is not None
    assert authorization.source == "web_search_result"
    assert authorization.source_call_id == "call-1"


def test_ledger_rejects_unsupported_schemes_and_ports() -> None:
    ledger = UrlAuthorizationLedger()
    ledger.register_user_text("file:///etc/passwd http://example.com:8080/x")
    assert ledger.authorize("file:///etc/passwd") is None
    assert ledger.authorize("http://example.com:8080/x") is None
