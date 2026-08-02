from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest
from agent.tools.builtin.social_platforms import (
    SocialAccessRestrictedError,
    SocialAuthExpiredError,
    SocialAuthRequiredError,
    SocialContentNotFoundError,
    SocialContentType,
    SocialNetworkError,
    SocialRateLimitedError,
    SocialSearchRequest,
)
from agent.tools.builtin.social_platforms.connectors import (
    BilibiliArticleLocator,
    BilibiliConnector,
    BilibiliCreatorLocator,
    BilibiliOpusLocator,
    BilibiliVideoLocator,
)


class _SdkError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(str(code))
        self.code = code


class _FakeApi:
    def __init__(self) -> None:
        self.authenticated = False
        self.search_calls: list[dict[str, object]] = []
        self.search_error: BaseException | None = None
        self.search_response: Mapping[str, Any] | None = None
        self.closed = False
        self.creator_works_error: BaseException | None = None
        self.creator_works_response: Mapping[str, Any] | None = None
        self.video = {
            "bvid": "BV1abc",
            "title": "视频标题",
            "desc": "视频简介",
            "pubdate": 1_700_000_000,
            "owner": {"name": "UP主"},
            "pages": [{"cid": 123}],
            "stat": {"view": 100, "like": 9, "coin": 3},
        }
        self.subtitle: Mapping[str, Any] | None = {
            "subtitles": [
                {"lan": "en", "subtitle_url": "https://example.test/en.json"},
                {"lan": "zh-CN", "subtitle_url": "//example.test/zh.json"},
            ]
        }
        self.subtitle_error: BaseException | None = None
        self.subtitle_urls: list[str] = []
        self.article: Mapping[str, Any] = {
            "id": 42,
            "title": "专栏标题",
            "author": {"name": "专栏作者"},
            "content": (
                "<style>.hidden{display:none}</style><p>第一段</p>"
                "<script>ignore this instruction</script><p>第二段 &amp; 更多</p>"
            ),
            "publish_time": 1_700_000_000,
            "stats": {"view": 20, "reply": 2},
        }
        self.opus_info: Mapping[str, Any] = {
            "item": {
                "id_str": "1201845223737524246",
                "modules": [
                    {
                        "module_author": {
                            "name": "图文作者",
                            "pub_ts": 1_700_000_000,
                        }
                    }
                ],
            }
        }
        self.opus_markdown = (
            "---\nitem:\n  id_str: '1201845223737524246'\n---\n\n# 图文标题\n\n第一段\n\n第二段"
        )

    def search(
        self,
        *,
        query: str,
        content_type: str,
        sort: str,
        published_after: str | None,
        published_before: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        self.search_calls.append(
            {
                "query": query,
                "content_type": content_type,
                "sort": sort,
                "published_after": published_after,
                "published_before": published_before,
                "limit": limit,
            }
        )
        if self.search_error is not None:
            raise self.search_error
        if self.search_response is not None:
            return self.search_response
        if content_type == "video":
            return {
                "result": [
                    {
                        "bvid": "BV1abc",
                        "aid": 123,
                        "title": '<em class="keyword">关键词</em>视频',
                        "author": "UP主",
                        "description": "视频<em>摘要</em>",
                        "pubdate": 1_700_000_000,
                        "play": 100,
                        "like": 9,
                    }
                ]
            }
        if content_type == "creator":
            return {
                "result": [
                    {
                        "mid": 8741868,
                        "uname": '<em class="keyword">开战斗机的虾米</em>',
                        "usign": "飞行模拟创作者",
                        "fans": 12345,
                        "videos": 88,
                        "upic": "https://i0.hdslb.com/avatar.jpg",
                    }
                ]
            }
        return {
            "result": [
                {
                    "id": 42,
                    "title": "<em>关键词</em>专栏",
                    "name": "专栏作者",
                    "desc": "专栏摘要",
                    "pub_time": 1_700_000_000,
                    "view": 20,
                }
            ]
        }

    def video_info(self, locator: BilibiliVideoLocator) -> Mapping[str, Any]:
        assert locator == BilibiliVideoLocator(bvid="BV1abc", aid=123)
        return self.video

    def video_subtitle(
        self, locator: BilibiliVideoLocator, *, cid: int
    ) -> Mapping[str, Any] | None:
        assert locator.bvid == "BV1abc"
        assert cid == 123
        if self.subtitle_error is not None:
            raise self.subtitle_error
        return self.subtitle

    def subtitle_body(self, url: str) -> Mapping[str, Any]:
        self.subtitle_urls.append(url)
        return {"body": [{"content": "第一句"}, {"content": "第二句"}]}

    def article_detail(self, locator: BilibiliArticleLocator) -> Mapping[str, Any]:
        assert locator == BilibiliArticleLocator(cvid=42)
        return self.article

    def opus_detail(self, locator: BilibiliOpusLocator) -> tuple[Mapping[str, Any], str]:
        assert locator == BilibiliOpusLocator(opus_id=1201845223737524246)
        return self.opus_info, self.opus_markdown

    def creator_profile(self, locator: BilibiliCreatorLocator) -> Mapping[str, Any]:
        assert locator == BilibiliCreatorLocator(uid=8741868)
        return {
            "mid": 8741868,
            "name": "开战斗机的虾米",
            "sign": "飞行模拟创作者",
            "level": 6,
        }

    def creator_works(
        self,
        locator: BilibiliCreatorLocator,
        *,
        sort: str,
        page: int,
        page_size: int,
    ) -> Mapping[str, Any]:
        assert locator == BilibiliCreatorLocator(uid=8741868)
        assert sort in {"popular", "latest"}
        assert page_size == 20
        if self.creator_works_error is not None:
            raise self.creator_works_error
        if self.creator_works_response is not None:
            return self.creator_works_response
        start = (page - 1) * page_size + 1
        stop = min(start + page_size, 22)
        return {
            "list": {
                "vlist": [
                    {
                        "bvid": f"BVcreator{index}",
                        "aid": 8700 + index,
                        "mid": 8741868,
                        "title": f"代表作 {index}",
                        "author": "开战斗机的虾米",
                        "created": 1_700_000_000 + index,
                        "play": 1000 - index,
                        "favorites": 100,
                        "length": "12:34",
                    }
                    for index in range(start, stop)
                ]
            },
            "page": {"count": 21, "pn": page, "ps": page_size},
        }

    def creator_masterpieces(
        self, locator: BilibiliCreatorLocator
    ) -> tuple[Mapping[str, Any], ...]:
        assert locator == BilibiliCreatorLocator(uid=8741868)
        return (
            {
                "bvid": "BVmast1",
                "aid": 9001,
                "title": "平台代表作",
                "owner": {"mid": 8741868, "name": "开战斗机的虾米"},
                "pubdate": 1_700_000_100,
                "stat": {"view": 9999, "like": 99},
                "duration": 321,
            },
        )

    def close(self) -> None:
        self.closed = True


def _request(
    *,
    content_types: tuple[SocialContentType, ...] = ("video", "article"),
    published_after: date | None = None,
    published_before: date | None = None,
) -> SocialSearchRequest:
    return SocialSearchRequest(
        platform="bilibili",
        query="关键词",
        content_types=content_types,
        sort="latest",
        published_after=published_after,
        published_before=published_before,
    )


def test_search_normalizes_video_and_article_candidates() -> None:
    api = _FakeApi()

    result = BilibiliConnector(api).search(
        _request(
            published_after=date(2026, 1, 1),
            published_before=date(2026, 2, 1),
        )
    )

    assert [candidate.title for candidate in result.candidates] == ["关键词视频", "关键词专栏"]
    assert result.candidates[0].canonical_url == "https://www.bilibili.com/video/BV1abc"
    assert result.candidates[0].metrics == {"play": 100, "like": 9}
    assert result.candidates[1].canonical_url == "https://www.bilibili.com/read/cv42"
    assert result.applied_filters == (
        "content_types",
        "sort",
        "published_after",
        "published_before",
    )
    assert result.unsupported_filters == ()
    assert api.search_calls[0]["published_after"] == "2026-01-01"
    assert api.search_calls[1]["published_before"] == "2026-02-01"


def test_search_reports_one_sided_date_as_unsupported() -> None:
    api = _FakeApi()

    result = BilibiliConnector(api).search(
        _request(content_types=("video",), published_after=date(2026, 1, 1))
    )

    assert result.unsupported_filters == ("published_after",)
    assert api.search_calls[0]["published_after"] is None


def test_search_creator_uses_user_identity_instead_of_video_keywords() -> None:
    result = BilibiliConnector(_FakeApi()).search(_request(content_types=("creator",)))

    candidate = result.candidates[0]
    assert candidate.locator == BilibiliCreatorLocator(uid=8741868)
    assert candidate.title == "开战斗机的虾米"
    assert candidate.canonical_url == "https://space.bilibili.com/8741868"
    assert candidate.identity == {
        "uid": 8741868,
        "avatar_url": "https://i0.hdslb.com/avatar.jpg",
    }
    assert candidate.metrics == {"fans": 12345, "videos": 88}


def test_search_treats_explicit_zero_results_without_result_key_as_empty() -> None:
    api = _FakeApi()
    api.search_response = {"numResults": 0, "page": 1, "pagesize": 10}

    result = BilibiliConnector(api).search(_request(content_types=("creator",)))

    assert result.candidates == ()


def test_creator_profile_and_popular_works_keep_uid_ownership() -> None:
    connector = BilibiliConnector(_FakeApi())

    profile = connector.read(BilibiliCreatorLocator(uid=8741868))
    works = connector.creator_works(
        BilibiliCreatorLocator(uid=8741868),
        sort="popular",
        page=1,
        page_size=20,
    )

    assert profile.title == "开战斗机的虾米"
    assert profile.evidence[0].kind == "creator_profile"
    assert '"uid": 8741868' in profile.evidence[0].text
    assert works.creator_url == "https://space.bilibili.com/8741868"
    assert works.works[0].canonical_url == "https://www.bilibili.com/video/BVcreator1"
    assert works.works[0].duration_seconds == 754
    assert works.works[0].uploader == "开战斗机的虾米"
    assert works.works[0].relationship == "direct_upload"
    assert works.has_more is True
    assert works.listing_scope == "all_public_works"


def test_popular_first_page_falls_back_to_sdk_masterpieces_on_space_failure() -> None:
    api = _FakeApi()
    api.creator_works_error = SocialNetworkError("space page rejected w_webid")

    works = BilibiliConnector(api).creator_works(
        BilibiliCreatorLocator(uid=8741868),
        sort="popular",
        page=1,
        page_size=20,
    )

    assert works.creator_name == "开战斗机的虾米"
    assert works.works[0].canonical_url == "https://www.bilibili.com/video/BVmast1"
    assert works.works[0].metrics == {"view": 9999, "like": 99}
    assert works.works[0].duration_seconds == 321
    assert works.has_more is False
    assert works.listing_scope == "curated_subset"


def test_latest_works_does_not_replace_failed_listing_with_masterpieces() -> None:
    api = _FakeApi()
    api.creator_works_error = SocialNetworkError("space page rejected w_webid")

    with pytest.raises(SocialNetworkError):
        BilibiliConnector(api).creator_works(
            BilibiliCreatorLocator(uid=8741868),
            sort="latest",
            page=1,
            page_size=20,
        )


def test_popular_first_page_falls_back_when_listing_shape_is_missing() -> None:
    api = _FakeApi()
    api.creator_works_response = {"page": {"count": 0}}

    works = BilibiliConnector(api).creator_works(
        BilibiliCreatorLocator(uid=8741868),
        sort="popular",
        page=1,
        page_size=20,
    )

    assert [item.canonical_url for item in works.works] == [
        "https://www.bilibili.com/video/BVmast1"
    ]


def test_creator_works_keeps_four_direct_and_three_union_videos() -> None:
    api = _FakeApi()
    rows = [
        {
            "bvid": f"BVdirect{index}",
            "mid": 8741868,
            "title": f"直接投稿 {index}",
            "author": "开战斗机的虾米",
            "is_union_video": 0,
        }
        for index in range(4)
    ]
    rows.extend(
        {
            "bvid": f"BVunion{index}",
            "mid": 16520572,
            "title": f"联合投稿 {index}",
            "author": "联合投稿主",
            "is_union_video": 1,
        }
        for index in range(3)
    )
    api.creator_works_response = {
        "list": {"vlist": rows},
        "page": {"count": 7},
    }

    works = BilibiliConnector(api).creator_works(
        BilibiliCreatorLocator(uid=8741868),
        sort="popular",
        page=1,
        page_size=20,
    )

    assert len(works.works) == 7
    assert [item.relationship for item in works.works] == [
        "direct_upload",
        "direct_upload",
        "direct_upload",
        "direct_upload",
        "collaboration",
        "collaboration",
        "collaboration",
    ]
    assert works.works[-1].uploader == "联合投稿主"
    assert works.has_more is False
    assert works.listing_scope == "all_public_works"


def test_creator_works_marks_partial_result_when_unverified_rows_are_dropped() -> None:
    api = _FakeApi()
    api.creator_works_response = {
        "list": {
            "vlist": [
                {
                    "bvid": "BVtarget",
                    "mid": 8741868,
                    "title": "目标投稿",
                    "author": "开战斗机的虾米",
                },
                {
                    "bvid": "BVforeign",
                    "mid": 16520572,
                    "title": "未标记联合投稿的外来视频",
                    "author": "另一位作者",
                    "is_union_video": 0,
                },
            ]
        },
        "page": {"count": 2},
    }

    works = BilibiliConnector(api).creator_works(
        BilibiliCreatorLocator(uid=8741868),
        sort="popular",
        page=1,
        page_size=20,
    )

    assert [item.canonical_url for item in works.works] == [
        "https://www.bilibili.com/video/BVtarget"
    ]
    assert works.listing_scope == "curated_subset"


def test_video_read_prefers_chinese_subtitle_and_keeps_description() -> None:
    api = _FakeApi()

    result = BilibiliConnector(api).read(BilibiliVideoLocator(bvid="BV1abc", aid=123))

    assert result.title == "视频标题"
    assert result.author == "UP主"
    assert result.canonical_url == "https://www.bilibili.com/video/BV1abc"
    assert [(item.kind, item.text) for item in result.evidence] == [
        ("video_description", "视频简介"),
        ("video_subtitle", "第一句\n第二句"),
    ]
    assert api.subtitle_urls == ["//example.test/zh.json"]
    assert result.metrics == {"view": 100, "like": 9}


def test_video_read_marks_missing_subtitle_without_fabricating_text() -> None:
    api = _FakeApi()
    api.subtitle = {"subtitles": []}

    result = BilibiliConnector(api).read(BilibiliVideoLocator(bvid="BV1abc", aid=123))

    assert [item.kind for item in result.evidence] == ["video_description"]
    assert result.unavailable_parts == ("video_subtitle",)


def test_video_read_keeps_description_when_only_subtitle_needs_login() -> None:
    api = _FakeApi()
    api.subtitle_error = SocialAuthRequiredError("SESSDATA is required")

    result = BilibiliConnector(api).read(BilibiliVideoLocator(bvid="BV1abc", aid=123))

    assert [item.kind for item in result.evidence] == ["video_description"]
    assert result.unavailable_parts == ("video_subtitle",)
    assert result.unavailable_reasons == {"video_subtitle": "auth_required"}


def test_article_read_turns_html_into_plain_evidence() -> None:
    result = BilibiliConnector(_FakeApi()).read(BilibiliArticleLocator(cvid=42))

    assert result.title == "专栏标题"
    assert result.author == "专栏作者"
    assert result.evidence[0].kind == "article_body"
    assert result.evidence[0].text == "第一段\n第二段 & 更多"
    assert result.unavailable_parts == ()


@pytest.mark.parametrize(
    ("url", "expected_locator", "expected_url"),
    [
        (
            "https://www.bilibili.com/video/BV1abc?spm_id_from=333.1",
            BilibiliVideoLocator(bvid="BV1abc", aid=None),
            "https://www.bilibili.com/video/BV1abc",
        ),
        (
            "https://m.bilibili.com/read/cv42?from=search",
            BilibiliArticleLocator(cvid=42),
            "https://www.bilibili.com/read/cv42",
        ),
        (
            "https://www.bilibili.com/opus/1201845223737524246?spm_id_from=333.1369.0.0",
            BilibiliOpusLocator(opus_id=1201845223737524246),
            "https://www.bilibili.com/opus/1201845223737524246",
        ),
        (
            "https://space.bilibili.com/8741868?spm_id_from=333.337.0.0",
            BilibiliCreatorLocator(uid=8741868),
            "https://space.bilibili.com/8741868",
        ),
    ],
)
def test_resolve_url_ignores_tracking_query_but_preserves_resource_identity(
    url: str,
    expected_locator: object,
    expected_url: str,
) -> None:
    resolved = BilibiliConnector(_FakeApi()).resolve_url(url)

    assert resolved is not None
    assert resolved.locator == expected_locator
    assert resolved.canonical_url == expected_url


def test_opus_read_uses_sdk_markdown_without_exposing_yaml_front_matter() -> None:
    result = BilibiliConnector(_FakeApi()).read(BilibiliOpusLocator(opus_id=1201845223737524246))

    assert result.title == "图文标题"
    assert result.author == "图文作者"
    assert result.canonical_url == "https://www.bilibili.com/opus/1201845223737524246"
    assert [(item.kind, item.text) for item in result.evidence] == [
        ("article_body", "第一段\n\n第二段")
    ]
    assert "id_str" not in result.evidence[0].text


@pytest.mark.parametrize(
    ("code", "authenticated", "expected"),
    [
        (-101, False, SocialAuthRequiredError),
        (-101, True, SocialAuthExpiredError),
        (-412, False, SocialAccessRestrictedError),
        (-509, False, SocialRateLimitedError),
        (-404, False, SocialContentNotFoundError),
    ],
)
def test_sdk_error_codes_are_mapped_without_returning_raw_details(
    code: int, authenticated: bool, expected: type[Exception]
) -> None:
    api = _FakeApi()
    api.authenticated = authenticated
    api.search_error = _SdkError(code)

    with pytest.raises(expected):
        BilibiliConnector(api).search(_request(content_types=("video",)))


def test_connector_close_delegates_to_sdk_adapter() -> None:
    api = _FakeApi()

    BilibiliConnector(api).close()

    assert api.closed is True
