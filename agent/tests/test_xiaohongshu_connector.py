from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pytest
from agent.tools.builtin.social_platforms import (
    CURRENT_SESSION_ID_ARG,
    SocialAccessRestrictedError,
    SocialAuthExpiredError,
    SocialAuthRequiredError,
    SocialConnectorIncompatibleError,
    SocialNetworkError,
    SocialPlatformService,
    SocialReadTool,
    SocialSearchRequest,
    SocialSearchTool,
)
from agent.tools.builtin.social_platforms.connectors import (
    McpCallResult,
    McpToolDefinition,
    OfficialMcpTransport,
    XiaohongshuConnector,
    XiaohongshuMcpApi,
    XiaohongshuNoteLocator,
    XiaohongshuProcess,
)
from agent.tools.builtin.social_platforms.connectors.xiaohongshu_artifacts import (
    XiaohongshuArtifact,
)


class _FakeApi:
    def __init__(self) -> None:
        self.has_login_material = False
        self.logged_in = True
        self.closed = False
        self.search_calls: list[dict[str, object]] = []
        self.detail_calls: list[dict[str, str]] = []
        self.search_error: BaseException | None = None

    def check_login(self) -> bool:
        return self.logged_in

    def search_feeds(self, *, keyword: str, filters: Mapping[str, str]) -> Mapping[str, Any]:
        self.search_calls.append({"keyword": keyword, "filters": dict(filters)})
        if self.search_error is not None:
            raise self.search_error
        return {
            "feeds": [
                {
                    "id": "feed-1",
                    "xsecToken": "secret-xsec-token",
                    "modelType": "note",
                    "noteCard": {
                        "displayTitle": "测试笔记",
                        "user": {"nickname": "作者"},
                        "interactInfo": {
                            "likedCount": "1.2万",
                            "commentCount": "9",
                        },
                    },
                },
                {
                    "id": "live-1",
                    "xsecToken": "ignored",
                    "modelType": "live_v2",
                    "noteCard": {},
                },
            ],
            "count": 2,
        }

    def get_feed_detail(self, *, feed_id: str, xsec_token: str) -> Mapping[str, Any]:
        self.detail_calls.append({"feed_id": feed_id, "xsec_token": xsec_token})
        return {
            "feed_id": feed_id,
            "data": {
                "note": {
                    "noteId": feed_id,
                    "title": "测试笔记",
                    "desc": "实际笔记正文",
                    "time": 1_700_000_000_000,
                    "user": {"nickname": "作者"},
                    "interactInfo": {
                        "likedCount": "1.2万",
                        "sharedCount": "3",
                        "commentCount": "9",
                        "collectedCount": "20",
                    },
                },
                "comments": {"list": []},
            },
        }

    def close(self) -> None:
        self.closed = True


def _request(
    *,
    after: date | None = None,
    before: date | None = None,
    sort: Literal["relevance", "latest", "popular"] = "popular",
) -> SocialSearchRequest:
    return SocialSearchRequest(
        platform="xiaohongshu",
        query="关键词",
        content_types=("note",),
        sort=sort,
        published_after=after,
        published_before=before,
    )


def test_search_maps_supported_filters_and_keeps_token_only_in_locator() -> None:
    api = _FakeApi()
    connector = XiaohongshuConnector(api, today=lambda: date(2026, 8, 1))

    result = connector.search(_request(after=date(2026, 7, 28)))

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.title == "测试笔记"
    assert candidate.author == "作者"
    assert candidate.canonical_url == "https://www.xiaohongshu.com/explore/feed-1"
    assert candidate.metrics == {"likes": 12_000, "comments": 9}
    assert candidate.locator == XiaohongshuNoteLocator(
        feed_id="feed-1", xsec_token="secret-xsec-token"
    )
    assert result.applied_filters == ("content_types", "sort", "published_after")
    assert result.unsupported_filters == ()
    filters = api.search_calls[0]["filters"]
    assert isinstance(filters, dict)
    assert filters["sort_by"] == "最多点赞"
    assert filters["publish_time"] == "一周内"


def test_search_reports_exact_historical_range_as_unsupported() -> None:
    api = _FakeApi()
    connector = XiaohongshuConnector(api, today=lambda: date(2026, 8, 1))

    result = connector.search(_request(after=date(2026, 1, 1), before=date(2026, 2, 1)))

    assert result.unsupported_filters == ("published_after", "published_before")
    filters = api.search_calls[0]["filters"]
    assert isinstance(filters, dict)
    assert "publish_time" not in filters


def test_search_omits_upstream_filters_for_default_choices() -> None:
    api = _FakeApi()
    connector = XiaohongshuConnector(api)

    connector.search(_request(sort="relevance"))

    assert api.search_calls == [{"keyword": "关键词", "filters": {}}]


def test_read_uses_search_locator_and_returns_note_body_only() -> None:
    api = _FakeApi()
    connector = XiaohongshuConnector(api)
    locator = XiaohongshuNoteLocator(feed_id="feed-1", xsec_token="secret-xsec-token")

    result = connector.read(locator)

    assert result.title == "测试笔记"
    assert result.author == "作者"
    assert result.canonical_url == "https://www.xiaohongshu.com/explore/feed-1"
    assert [(item.kind, item.text) for item in result.evidence] == [("note_body", "实际笔记正文")]
    assert result.metrics == {
        "likes": 12_000,
        "shares": 3,
        "comments": 9,
        "collections": 20,
    }
    assert api.detail_calls == [{"feed_id": "feed-1", "xsec_token": "secret-xsec-token"}]


def test_resolve_url_keeps_xsec_token_only_in_connector_locator() -> None:
    resolved = XiaohongshuConnector(_FakeApi()).resolve_url(
        "https://www.xiaohongshu.com/discovery/item/feed-1"
        "?xsec_token=secret-xsec-token&xsec_source=pc_search"
    )

    assert resolved is not None
    assert resolved.canonical_url == "https://www.xiaohongshu.com/explore/feed-1"
    assert resolved.locator == XiaohongshuNoteLocator(
        feed_id="feed-1", xsec_token="secret-xsec-token"
    )


def test_resolve_url_without_xsec_token_can_match_identity_but_is_not_readable() -> None:
    resolved = XiaohongshuConnector(_FakeApi()).resolve_url(
        "https://www.xiaohongshu.com/explore/feed-1"
    )

    assert resolved is not None
    assert resolved.canonical_url == "https://www.xiaohongshu.com/explore/feed-1"
    assert resolved.locator is None


def test_tool_results_never_expose_xsec_token_or_local_endpoint() -> None:
    connector = XiaohongshuConnector(_FakeApi())
    service = SocialPlatformService((connector,))
    search = SocialSearchTool(service).invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    resource_ref = json.loads(search.text)["results"][0]["resource_ref"]

    read = SocialReadTool(service).invoke(
        {
            "resource_ref": resource_ref,
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    combined = search.text + read.text + json.dumps(search.meta) + json.dumps(read.meta)
    assert "secret-xsec-token" not in combined
    assert "127.0.0.1" not in combined


@pytest.mark.parametrize(
    ("has_material", "expected"),
    [(False, SocialAuthRequiredError), (True, SocialAuthExpiredError)],
)
def test_login_state_is_not_reported_as_no_results(
    has_material: bool, expected: type[Exception]
) -> None:
    api = _FakeApi()
    api.logged_in = False
    api.has_login_material = has_material

    with pytest.raises(expected):
        XiaohongshuConnector(api).search(_request())

    assert api.search_calls == []


def test_access_restriction_is_preserved() -> None:
    api = _FakeApi()
    api.search_error = SocialAccessRestrictedError("raw platform details")

    with pytest.raises(SocialAccessRestrictedError):
        XiaohongshuConnector(api).search(_request())


class _FakeProcess:
    def __init__(self) -> None:
        self.has_login_material = False
        self.closed = False

    def ensure_started(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def close(self) -> None:
        self.closed = True


def _tool(
    name: str,
    properties: Mapping[str, Mapping[str, str]],
    required: list[str] | None = None,
) -> McpToolDefinition:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties)}
    if required is not None:
        schema["required"] = required
    return McpToolDefinition(name=name, input_schema=schema)


def _compatible_tools() -> tuple[McpToolDefinition, ...]:
    return (
        _tool("check_login_status", {}),
        _tool(
            "search_feeds",
            {"keyword": {"type": "string"}, "filters": {"type": "object"}},
            ["keyword"],
        ),
        _tool(
            "get_feed_detail",
            {
                "feed_id": {"type": "string"},
                "xsec_token": {"type": "string"},
                "load_all_comments": {"type": "boolean"},
            },
            ["feed_id", "xsec_token"],
        ),
        _tool("publish_content", {"content": {"type": "string"}}, ["content"]),
    )


class _FakeTransport:
    def __init__(self, tools: tuple[McpToolDefinition, ...] | None = None) -> None:
        self.tools = tools or _compatible_tools()
        self.list_calls = 0
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def list_tools(self, endpoint: str) -> tuple[McpToolDefinition, ...]:
        assert endpoint == "http://127.0.0.1:12345/mcp"
        self.list_calls += 1
        return self.tools

    def call_tool(self, endpoint: str, name: str, arguments: Mapping[str, Any]) -> McpCallResult:
        assert endpoint == "http://127.0.0.1:12345/mcp"
        self.calls.append((name, dict(arguments)))
        if name == "check_login_status":
            return McpCallResult(text="✅ 已登录", is_error=False)
        if name == "search_feeds":
            return McpCallResult(text='{"feeds": [], "count": 0}', is_error=False)
        raise AssertionError(f"unexpected operation: {name}")


def test_mcp_api_ignores_write_tools_and_only_calls_fixed_read_operations() -> None:
    transport = _FakeTransport()
    api = XiaohongshuMcpApi(_FakeProcess(), transport)

    assert api.check_login() is True
    assert api.search_feeds(keyword="关键词", filters={"sort_by": "综合"}) == {
        "feeds": [],
        "count": 0,
    }

    assert transport.list_calls == 1
    assert [name for name, _ in transport.calls] == [
        "check_login_status",
        "search_feeds",
    ]
    assert all(name != "publish_content" for name, _ in transport.calls)


def test_mcp_schema_drift_fails_before_any_tool_call() -> None:
    drifted = tuple(
        tool for tool in _compatible_tools() if tool.name not in {"search_feeds", "publish_content"}
    )
    transport = _FakeTransport(drifted)
    api = XiaohongshuMcpApi(_FakeProcess(), transport)

    with pytest.raises(SocialConnectorIncompatibleError):
        api.check_login()

    assert transport.calls == []


def test_nested_mcp_timeout_is_retryable_network_error() -> None:
    class TimeoutTransport(_FakeTransport):
        def call_tool(
            self,
            endpoint: str,
            name: str,
            arguments: Mapping[str, Any],
        ) -> McpCallResult:
            if name == "check_login_status":
                return super().call_tool(endpoint, name, arguments)
            raise ExceptionGroup(
                "MCP task group failed",
                [RuntimeError("Request 'tools/call' timed out")],
            )

    api = XiaohongshuMcpApi(_FakeProcess(), TimeoutTransport())

    assert api.check_login() is True
    with pytest.raises(SocialNetworkError) as caught:
        api.search_feeds(keyword="关键词", filters={})

    assert caught.value.is_retryable is True


def test_official_transport_can_extend_search_timeout_only() -> None:
    transport = OfficialMcpTransport(
        read_timeout_seconds=45,
        search_timeout_seconds=75,
    )

    assert transport._tool_timeout_seconds("search_feeds") == 75
    assert transport._tool_timeout_seconds("check_login_status") == 45
    assert transport._tool_timeout_seconds("get_feed_detail") == 45


def test_official_transport_defaults_to_search_specific_timeout() -> None:
    transport = OfficialMcpTransport()

    assert transport._tool_timeout_seconds("search_feeds") == 75
    assert transport._tool_timeout_seconds("check_login_status") == 45


@pytest.mark.parametrize("timeout", [0, -1])
def test_official_transport_rejects_non_positive_search_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeouts must be positive"):
        OfficialMcpTransport(search_timeout_seconds=timeout)


def test_upstream_page_deadline_is_retryable_network_error() -> None:
    class DeadlineTransport(_FakeTransport):
        def call_tool(
            self,
            endpoint: str,
            name: str,
            arguments: Mapping[str, Any],
        ) -> McpCallResult:
            if name == "check_login_status":
                return super().call_tool(endpoint, name, arguments)
            return McpCallResult(
                text=("工具 search_feeds 执行时发生内部错误: context deadline exceeded"),
                is_error=True,
            )

    api = XiaohongshuMcpApi(_FakeProcess(), DeadlineTransport())

    assert api.check_login() is True
    with pytest.raises(SocialNetworkError) as caught:
        api.search_feeds(keyword="关键词", filters={})

    assert caught.value.is_retryable is True


class _FakeChild:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


def test_process_is_lazy_singleton_loopback_and_close_owned(tmp_path: Path) -> None:
    server = XiaohongshuArtifact("server", "test", "test", "server", 0, "")
    login = XiaohongshuArtifact("login", "test", "test", "login", 0, "")
    (tmp_path / "server").write_bytes(b"")
    (tmp_path / "login").write_bytes(b"")
    children: list[_FakeChild] = []
    commands: list[list[str]] = []

    def spawn(command: list[str], **kwargs: object) -> Any:
        del kwargs
        commands.append(command)
        child = _FakeChild()
        children.append(child)
        return child

    process = XiaohongshuProcess(
        artifact_dir=tmp_path,
        cookies_path=tmp_path / "private" / "cookies.json",
        popen=spawn,
        allocate_port=lambda: 23456,
        verify_artifacts=False,
        artifacts=(server, login),
    )

    first = process.ensure_started()
    second = process.ensure_started()

    assert first == second == "http://127.0.0.1:23456/mcp"
    assert len(children) == 1
    assert commands[0][1:] == ["-port=127.0.0.1:23456", "-headless=true"]

    process.close()

    assert children[0].terminated is True


def test_connector_close_closes_mcp_api() -> None:
    api = _FakeApi()

    XiaohongshuConnector(api).close()

    assert api.closed is True
