from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import count
from typing import ClassVar

import pytest
from agent.tools.builtin.social_platforms import (
    CURRENT_DESTINATION_ID_ARG,
    CURRENT_SESSION_ID_ARG,
    CURRENT_SURFACE_ARG,
    CURRENT_URL_AUTHORIZATIONS_ARG,
    RequestSocialAuthTool,
    SocialAuthRequiredError,
    SocialContentType,
    SocialEvidence,
    SocialPlatform,
    SocialPlatformService,
    SocialReadResponse,
    SocialReadTool,
    SocialResolvedUrl,
    SocialResourceEntry,
    SocialResourceExpiredError,
    SocialResourceNotFoundError,
    SocialResourceStore,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
    SocialSearchTool,
)


@dataclass(frozen=True)
class _NoteLocator:
    platform: SocialPlatform = "xiaohongshu"
    content_type: SocialContentType = "note"
    feed_id: str = "feed-1"
    xsec_token: str = "secret-xsec-token"


class _XhsConnector:
    platform: ClassVar[SocialPlatform] = "xiaohongshu"
    name: ClassVar[str] = "fake-xhs"
    version: ClassVar[str] = "1.0"

    def __init__(self) -> None:
        self.search_calls: list[SocialSearchRequest] = []
        self.read_calls: list[object] = []
        self.closed = False
        self.search_error: BaseException | None = None
        self.read_response: SocialReadResponse | None = None

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        self.search_calls.append(request)
        if self.search_error is not None:
            raise self.search_error
        return SocialSearchResponse(
            candidates=(
                SocialSearchCandidate(
                    locator=_NoteLocator(),
                    title="测试笔记",
                    author="作者",
                    canonical_url="https://www.xiaohongshu.com/explore/feed-1",
                    summary="摘要",
                    metrics={"likes": 3},
                ),
            ),
            applied_filters=("sort",),
            unsupported_filters=("published_after",),
        )

    def read(self, locator: object) -> SocialReadResponse:
        self.read_calls.append(locator)
        return self.read_response or SocialReadResponse(
            title="测试笔记",
            author="作者",
            canonical_url="https://www.xiaohongshu.com/explore/feed-1",
            evidence=(SocialEvidence(kind="note_body", text="正文内容"),),
            metrics={"likes": 3},
        )

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        if not url.startswith("https://www.xiaohongshu.com/explore/feed-1"):
            return None
        locator = _NoteLocator() if "xsec_token=secret-xsec-token" in url else None
        return SocialResolvedUrl(
            platform="xiaohongshu",
            content_type="note",
            canonical_url="https://www.xiaohongshu.com/explore/feed-1",
            locator=locator,
        )

    def close(self) -> None:
        self.closed = True


class _BiliConnector:
    platform: ClassVar[SocialPlatform] = "bilibili"
    name: ClassVar[str] = "fake-bili"
    version: ClassVar[str] = "1.0"

    def __init__(self) -> None:
        self.search_calls: list[SocialSearchRequest] = []

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        self.search_calls.append(request)
        return SocialSearchResponse(candidates=())

    def read(self, locator: object) -> SocialReadResponse:
        raise AssertionError("read should not be called")

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        return None

    def close(self) -> None:
        pass


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _service(connector: _XhsConnector, *, clock: _Clock | None = None) -> SocialPlatformService:
    refs = iter(
        [
            "social_ref_00000000000000000000000000000001",
            "social_ref_00000000000000000000000000000002",
        ]
    )
    resources = SocialResourceStore(
        clock=clock or _Clock(),
        make_ref=lambda: next(refs),
    )
    return SocialPlatformService((connector,), resources=resources)


def test_search_returns_opaque_reference_without_connector_secrets() -> None:
    connector = _XhsConnector()
    tool = SocialSearchTool(_service(connector))

    result = tool.invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            "sort": "popular",
            "published_after": "2026-01-01",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["source_type"] == "social_search"
    assert payload["trust"] == "untrusted_data"
    assert payload["status"] == "ok"
    assert payload["results"][0]["resource_ref"].startswith("social_ref_")
    assert payload["results"][0]["url"].startswith("https://www.xiaohongshu.com/")
    assert payload["applied_filters"] == ["sort"]
    assert payload["unsupported_filters"] == ["published_after"]
    assert "secret-xsec-token" not in result.text
    assert connector.search_calls[0].content_types == ("note",)
    assert connector.search_calls[0].limit == 20
    assert payload["search_scope"] == {
        "ranking": "personalized_time_varying_snapshot",
        "returned_count": 1,
        "absence_not_conclusive": True,
    }


def test_search_uses_fixed_platform_limits_without_exposing_them_to_the_model() -> None:
    connector = _BiliConnector()
    tool = SocialSearchTool(SocialPlatformService((connector,)))

    result = tool.invoke(
        {
            "platform": "bilibili",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    assert result.is_error is False
    assert connector.search_calls[0].limit == 10
    assert "limit" not in SocialSearchTool.parameters_schema["properties"]
    assert json.loads(result.text)["search_scope"] == {
        "ranking": "time_varying_ranked_snapshot",
        "returned_count": 0,
        "absence_not_conclusive": True,
    }
    assert "完全相同的参数重复搜索" in SocialSearchTool.description
    assert "最多再调用一次不同的精炼查询" in SocialSearchTool.description


def test_read_resolves_reference_only_within_the_same_session() -> None:
    connector = _XhsConnector()
    service = _service(connector)
    search = SocialSearchTool(service).invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    resource_ref = json.loads(search.text)["results"][0]["resource_ref"]

    other_session = SocialReadTool(service).invoke(
        {"resource_ref": resource_ref, CURRENT_SESSION_ID_ARG: "session-b"}
    )
    same_session = SocialReadTool(service).invoke(
        {"resource_ref": resource_ref, CURRENT_SESSION_ID_ARG: "session-a"}
    )

    assert other_session.is_error is True
    assert json.loads(other_session.text)["status"] == "resource_not_found"
    assert same_session.is_error is False
    payload = json.loads(same_session.text)
    assert payload["evidence"] == [{"kind": "note_body", "text": "正文内容"}]
    assert "secret-xsec-token" not in same_session.text
    assert connector.read_calls == [_NoteLocator()]


def test_read_accepts_only_same_resource_from_hidden_current_turn_url_authorization() -> None:
    connector = _XhsConnector()
    tool = SocialReadTool(_service(connector))
    authorized_url = (
        "https://www.xiaohongshu.com/explore/feed-1"
        "?xsec_token=secret-xsec-token&xsec_source=pc_search"
    )

    result = tool.invoke(
        {
            "url": "https://www.xiaohongshu.com/explore/feed-1",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_URL_AUTHORIZATIONS_ARG: [authorized_url],
        }
    )

    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["resource_ref"] is None
    assert payload["url"] == "https://www.xiaohongshu.com/explore/feed-1"
    assert "secret-xsec-token" not in result.text
    assert connector.read_calls == [_NoteLocator()]


def test_read_rejects_untrusted_or_ambiguous_url_before_connector_read() -> None:
    connector = _XhsConnector()
    tool = SocialReadTool(_service(connector))

    unauthorized = tool.invoke(
        {
            "url": "https://www.xiaohongshu.com/explore/feed-1",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_URL_AUTHORIZATIONS_ARG: [],
        }
    )
    ambiguous = tool.invoke(
        {
            "resource_ref": "social_ref_00000000000000000000000000000001",
            "url": "https://www.xiaohongshu.com/explore/feed-1",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_URL_AUTHORIZATIONS_ARG: [],
        }
    )

    assert json.loads(unauthorized.text)["status"] == "url_not_authorized"
    assert json.loads(ambiguous.text)["status"] == "invalid_arguments"
    assert connector.read_calls == []
    assert SocialReadTool.parameters_schema["oneOf"] == [
        {"required": ["resource_ref"]},
        {"required": ["url"]},
    ]
    assert "直链时改用 social_read" in SocialSearchTool.description
    assert "不要把 web_fetch 描述成" in SocialReadTool.description


def test_social_search_timeout_covers_xiaohongshu_preflight_and_search() -> None:
    assert SocialSearchTool.timeout_seconds == 90.0
    assert SocialReadTool.timeout_seconds == 45.0


def test_read_reports_supported_xhs_url_without_required_token_as_unsupported() -> None:
    connector = _XhsConnector()
    tool = SocialReadTool(_service(connector))
    url = "https://www.xiaohongshu.com/explore/feed-1"

    result = tool.invoke(
        {
            "url": url,
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_URL_AUTHORIZATIONS_ARG: [url],
        }
    )

    assert result.is_error
    assert json.loads(result.text)["status"] == "url_unsupported"
    assert connector.read_calls == []


def test_partial_read_exposes_auth_decision_for_explicit_login_goal() -> None:
    connector = _XhsConnector()
    connector.read_response = SocialReadResponse(
        title="测试笔记",
        author="作者",
        canonical_url="https://www.xiaohongshu.com/explore/feed-1",
        evidence=(SocialEvidence(kind="search_summary", text="只有摘要"),),
        unavailable_parts=("note_body",),
        unavailable_reasons={"note_body": "auth_required"},
    )
    service = _service(connector)
    search = SocialSearchTool(service).invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    resource_ref = json.loads(search.text)["results"][0]["resource_ref"]

    result = SocialReadTool(service).invoke(
        {"resource_ref": resource_ref, CURRENT_SESSION_ID_ARG: "session-a"}
    )

    payload = json.loads(result.text)
    assert payload["status"] == "partial"
    assert payload["authorization_option"]["tool"] == "request_social_auth"
    assert "用户明确要求登录" in payload["authorization_option"]["decision"]
    assert "登录本身就是当前目标" in RequestSocialAuthTool.description


def test_unknown_method_argument_is_rejected_before_connector_call() -> None:
    connector = _XhsConnector()
    tool = SocialSearchTool(_service(connector))

    result = tool.invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            "method": "publish_content",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    assert result.is_error is True
    assert json.loads(result.text)["status"] == "invalid_arguments"
    assert connector.search_calls == []
    assert "method" not in SocialSearchTool.parameters_schema["properties"]


def test_auth_required_is_not_reported_as_no_results() -> None:
    connector = _XhsConnector()
    connector.search_error = SocialAuthRequiredError("cookie value must remain hidden")

    result = SocialSearchTool(_service(connector)).invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    assert result.is_error is True
    payload = json.loads(result.text)
    assert payload["status"] == "auth_required"
    assert payload["platform"] == "xiaohongshu"
    assert payload["evidence_gap"]["missing"] == [
        {
            "capability": "note_search",
            "reason": "auth_required",
            "recoverable_by": "local_login",
        }
    ]
    assert payload["authorization_option"]["tool"] == "request_social_auth"
    assert "cookie value" not in result.text


def test_auth_request_tool_creates_pending_desktop_request_without_starting_login() -> None:
    service = _service(_XhsConnector())
    tool = RequestSocialAuthTool(service)

    result = tool.invoke(
        {
            "platform": "xiaohongshu",
            "missing_capabilities": ["note_body"],
            "reason": "需要正文才能核对用户询问的细节",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_SURFACE_ARG: "desktop_chat",
        }
    )

    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["status"] == "pending_user_authorization"
    assert payload["authorization_surface"] == "desktop"
    request = service.auth_request(payload["request_id"])
    assert request.status == "pending"
    assert request.origin_surface == "desktop_chat"
    assert request.origin_destination_id is None


def test_auth_request_tool_freezes_im_destination_and_deduplicates_request() -> None:
    service = _service(_XhsConnector())
    tool = RequestSocialAuthTool(service)
    args = {
        "platform": "xiaohongshu",
        "missing_capabilities": ["note_body"],
        "reason": "需要正文才能回答",
        CURRENT_SESSION_ID_ARG: "session-a",
        CURRENT_SURFACE_ARG: "im:qq",
        CURRENT_DESTINATION_ID_ARG: "qq|bind|c2c|chat|session-a",
    }

    first = json.loads(tool.invoke(args).text)
    second = json.loads(tool.invoke({**args, "reason": "另一个说法"}).text)

    assert first["request_id"] == second["request_id"]
    assert first["origin_surface"] == "im:qq"
    assert "电脑端" in first["message"]
    request = service.auth_request(first["request_id"])
    assert request.origin_destination_id == "qq|bind|c2c|chat|session-a"


@pytest.mark.parametrize(
    "args",
    [
        {
            "platform": "xiaohongshu",
            "missing_capabilities": ["video_subtitle"],
            "reason": "能力属于错误平台",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_SURFACE_ARG: "desktop_chat",
        },
        {
            "platform": "xiaohongshu",
            "missing_capabilities": ["note_body"],
            "reason": "IM 缺少可信回传目标",
            CURRENT_SESSION_ID_ARG: "session-a",
            CURRENT_SURFACE_ARG: "im:qq",
        },
    ],
)
def test_auth_request_tool_fails_closed_on_invalid_capability_or_im_origin(
    args: dict[str, object],
) -> None:
    result = RequestSocialAuthTool(_service(_XhsConnector())).invoke(args)

    assert result.is_error is True
    assert json.loads(result.text)["status"] == "invalid_arguments"


def test_resource_store_reports_expiry_and_hides_cross_session_tombstone() -> None:
    clock = _Clock()
    store = SocialResourceStore(
        ttl_seconds=10,
        clock=clock,
        make_ref=lambda: "social_ref_00000000000000000000000000000001",
    )
    entry = store.put(
        session_id="session-a",
        platform="xiaohongshu",
        content_type="note",
        canonical_url="https://www.xiaohongshu.com/explore/feed-1",
        locator=_NoteLocator(),
        connector_version="1.0",
    )
    clock.now = 11

    try:
        store.get(session_id="session-a", resource_ref=entry.resource_ref)
    except SocialResourceExpiredError:
        pass
    else:
        raise AssertionError("expected resource expiry")

    try:
        store.get(session_id="session-b", resource_ref=entry.resource_ref)
    except SocialResourceNotFoundError:
        pass
    else:
        raise AssertionError("cross-session lookup must not reveal expiry")


def test_resource_store_evicts_oldest_entries_at_session_and_global_limits() -> None:
    refs = iter(
        [
            "social_ref_00000000000000000000000000000001",
            "social_ref_00000000000000000000000000000002",
            "social_ref_00000000000000000000000000000003",
            "social_ref_00000000000000000000000000000004",
        ]
    )
    store = SocialResourceStore(
        max_per_session=2,
        max_total=2,
        clock=_Clock(),
        make_ref=lambda: next(refs),
    )

    first = store.put(
        session_id="session-a",
        platform="xiaohongshu",
        content_type="note",
        canonical_url="https://www.xiaohongshu.com/explore/1",
        locator=_NoteLocator(feed_id="1"),
        connector_version="1.0",
    )
    second = store.put(
        session_id="session-a",
        platform="xiaohongshu",
        content_type="note",
        canonical_url="https://www.xiaohongshu.com/explore/2",
        locator=_NoteLocator(feed_id="2"),
        connector_version="1.0",
    )
    third = store.put(
        session_id="session-a",
        platform="xiaohongshu",
        content_type="note",
        canonical_url="https://www.xiaohongshu.com/explore/3",
        locator=_NoteLocator(feed_id="3"),
        connector_version="1.0",
    )
    fourth = store.put(
        session_id="session-b",
        platform="xiaohongshu",
        content_type="note",
        canonical_url="https://www.xiaohongshu.com/explore/4",
        locator=_NoteLocator(feed_id="4"),
        connector_version="1.0",
    )

    for session_id, resource_ref in (
        ("session-a", first.resource_ref),
        ("session-a", second.resource_ref),
    ):
        with pytest.raises(SocialResourceNotFoundError):
            store.get(session_id=session_id, resource_ref=resource_ref)
    assert store.get(session_id="session-a", resource_ref=third.resource_ref) == third
    assert store.get(session_id="session-b", resource_ref=fourth.resource_ref) == fourth


def test_resource_store_is_safe_under_concurrent_put_and_get() -> None:
    sequence = count(1)
    store = SocialResourceStore(
        max_per_session=100,
        max_total=100,
        clock=_Clock(),
        make_ref=lambda: f"social_ref_{next(sequence):032x}",
    )

    def put(index: int) -> SocialResourceEntry:
        return store.put(
            session_id="session-a",
            platform="xiaohongshu",
            content_type="note",
            canonical_url=f"https://www.xiaohongshu.com/explore/{index}",
            locator=_NoteLocator(feed_id=str(index)),
            connector_version="1.0",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        entries = list(executor.map(put, range(50)))
        resolved = list(
            executor.map(
                lambda entry: store.get(session_id="session-a", resource_ref=entry.resource_ref),
                entries,
            )
        )

    assert {entry.resource_ref for entry in resolved} == {entry.resource_ref for entry in entries}


def test_read_truncates_large_evidence_without_losing_tail() -> None:
    connector = _XhsConnector()
    connector.read_response = SocialReadResponse(
        title="long",
        author="author",
        canonical_url="https://www.xiaohongshu.com/explore/feed-1",
        evidence=(SocialEvidence(kind="note_body", text="a" * 20_000 + "TAIL"),),
    )
    service = _service(connector)
    search = SocialSearchTool(service).invoke(
        {
            "platform": "xiaohongshu",
            "query": "关键词",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    resource_ref = json.loads(search.text)["results"][0]["resource_ref"]

    result = SocialReadTool(service).invoke(
        {"resource_ref": resource_ref, CURRENT_SESSION_ID_ARG: "session-a"}
    )

    payload = json.loads(result.text)
    assert payload["truncated"] is True
    assert payload["original_char_count"] == 20_004
    assert len(payload["evidence"][0]["text"]) == 20_000
    assert payload["evidence"][0]["text"].endswith("TAIL")


def test_service_close_closes_connector() -> None:
    connector = _XhsConnector()
    service = _service(connector)

    service.close()

    assert connector.closed is True
