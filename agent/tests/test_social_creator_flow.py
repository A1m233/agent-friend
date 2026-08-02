from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from agent.tools.builtin.social_platforms import (
    CURRENT_SESSION_ID_ARG,
    CURRENT_URL_AUTHORIZATIONS_ARG,
    SocialContentType,
    SocialCreatorWork,
    SocialCreatorWorksResponse,
    SocialEvidence,
    SocialPlatform,
    SocialPlatformService,
    SocialReadResponse,
    SocialReadTool,
    SocialResolvedUrl,
    SocialResourceLocator,
    SocialResourceStore,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
    SocialSearchTool,
)
from agent.tools.builtin.social_platforms.resources import SocialContinuationStore
from agent.tools.builtin.social_platforms.tool import RESOLVED_EVIDENCE_URL_ARG


@dataclass(frozen=True)
class _CreatorLocator:
    uid: int
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="creator")


@dataclass(frozen=True)
class _VideoLocator:
    bvid: str
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="video")


class _CreatorConnector:
    platform: ClassVar[SocialPlatform] = "bilibili"
    name: ClassVar[str] = "fake-bilibili"
    version: ClassVar[str] = "1"

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        assert request.content_types == ("creator",)
        return SocialSearchResponse(
            candidates=(
                SocialSearchCandidate(
                    locator=_CreatorLocator(8741868),
                    title="开战斗机的虾米",
                    author="开战斗机的虾米",
                    canonical_url="https://space.bilibili.com/8741868",
                    summary="飞行模拟创作者",
                    identity={"uid": 8741868},
                ),
            )
        )

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        if url.split("?", 1)[0] != "https://space.bilibili.com/8741868":
            return None
        return SocialResolvedUrl(
            platform="bilibili",
            content_type="creator",
            canonical_url="https://space.bilibili.com/8741868",
            locator=_CreatorLocator(8741868),
        )

    def read(self, locator: SocialResourceLocator) -> SocialReadResponse:
        assert locator == _CreatorLocator(8741868)
        return SocialReadResponse(
            title="开战斗机的虾米",
            author="开战斗机的虾米",
            canonical_url="https://space.bilibili.com/8741868",
            evidence=(SocialEvidence("creator_profile", '{"uid": 8741868}'),),
        )

    def creator_works(
        self,
        locator: object,
        *,
        sort: Literal["popular", "latest"],
        page: int,
        page_size: int,
    ) -> SocialCreatorWorksResponse:
        assert locator == _CreatorLocator(8741868)
        assert page_size == 20
        return SocialCreatorWorksResponse(
            creator_name="开战斗机的虾米",
            creator_url="https://space.bilibili.com/8741868",
            works=(
                SocialCreatorWork(
                    locator=_VideoLocator(f"BV{page}"),
                    title=f"代表作 {page}",
                    canonical_url=f"https://www.bilibili.com/video/BV{page}",
                    metrics={"play": 1000 - page},
                    uploader="开战斗机的虾米",
                ),
            ),
            sort=sort,
            page=page,
            has_more=page == 1,
        )

    def close(self) -> None:
        return None


def _service() -> SocialPlatformService:
    resource_refs = iter(f"social_ref_{index:032x}" for index in range(1, 10))
    continuation_refs = iter(["continuation_ref_00000000000000000000000000000001"])
    return SocialPlatformService(
        (_CreatorConnector(),),
        resources=SocialResourceStore(make_ref=lambda: next(resource_refs)),
        continuations=SocialContinuationStore(make_ref=lambda: next(continuation_refs)),
    )


def test_creator_search_works_and_opaque_continuation_flow() -> None:
    service = _service()
    search = SocialSearchTool(service).invoke(
        {
            "platform": "bilibili",
            "query": "开战斗机的虾米",
            "content_types": ["creator"],
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    search_payload = json.loads(search.text)
    creator = search_payload["results"][0]

    first = SocialReadTool(service).invoke(
        {
            "resource_ref": creator["resource_ref"],
            "view": "works",
            "sort": "popular",
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    first_payload = json.loads(first.text)
    second = SocialReadTool(service).invoke(
        {
            "continuation_ref": first_payload["continuation_ref"],
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )
    second_payload = json.loads(second.text)

    assert creator["identity"] == {"uid": 8741868}
    assert creator["available_views"] == ["detail", "works"]
    assert creator["evidence_ref"].startswith("evidence_ref_")
    assert first_payload["works"][0]["title"] == "代表作 1"
    assert first_payload["works"][0]["resource_ref"].startswith("social_ref_")
    assert first_payload["works"][0]["evidence_ref"].startswith("evidence_ref_")
    assert first_payload["works"][0]["uploader"] == "开战斗机的虾米"
    assert first_payload["works"][0]["relationship"] == "direct_upload"
    assert first_payload["listing_scope"] == "all_public_works"
    assert first_payload["is_complete"] is False
    assert second_payload["page"] == 2
    assert second_payload["continuation_ref"] is None
    assert second_payload["is_complete"] is True


def test_creator_tool_contract_routes_representative_works_explicitly() -> None:
    view = SocialReadTool.parameters_schema["properties"]["view"]
    sort = SocialReadTool.parameters_schema["properties"]["sort"]

    assert "代表作" in SocialReadTool.description
    assert "必须用 view=works" in SocialReadTool.description
    assert "代表作" in view["description"]
    assert "popular" in sort["description"]
    assert "全部" in sort["description"]
    assert "curated_subset" in SocialReadTool.description


def test_creator_works_can_start_from_persisted_public_evidence() -> None:
    service = _service()
    url = "https://space.bilibili.com/8741868"

    result = SocialReadTool(service).invoke(
        {
            "evidence_ref": "evidence_ref_00000000000000000000000000000001",
            "view": "works",
            "sort": "latest",
            RESOLVED_EVIDENCE_URL_ARG: url,
            CURRENT_URL_AUTHORIZATIONS_ARG: [url],
            CURRENT_SESSION_ID_ARG: "session-a",
        }
    )

    payload = json.loads(result.text)
    assert payload["sort"] == "latest"
    assert payload["works"][0]["url"] == "https://www.bilibili.com/video/BV1"
