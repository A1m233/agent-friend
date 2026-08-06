"""NearbyPlaceSearchTool 的意图编排与证据边界测试。"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest
from agent.tools.builtin.nearby_place_search import (
    AnchorCandidate,
    NearbyPlace,
    NearbyPlaceAuthenticationError,
    NearbyPlaceProvider,
    NearbyPlaceSearchTool,
    NearbyPlaceTemporaryError,
    PlaceSort,
)


def _anchor(
    ref: str = "anchor-1",
    name: str = "福民银行",
    address: str = "广东省深圳市福田区福民路1号",
) -> AnchorCandidate:
    return AnchorCandidate(
        ref=ref,
        name=name,
        address=address,
        city="深圳市",
        district="福田区",
        longitude=114.055,
        latitude=22.521,
    )


class _StubProvider:
    name: ClassVar[str] = "stub"

    def __init__(
        self,
        *,
        anchors: tuple[AnchorCandidate, ...] = (),
        places: tuple[NearbyPlace, ...] = (),
        refs: dict[str, AnchorCandidate] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.anchors = anchors
        self.places = places
        self.refs = refs or {}
        self.raises = raises
        self.anchor_calls: list[tuple[str, str | None]] = []
        self.ref_calls: list[str] = []
        self.nearby_calls: list[tuple[str, AnchorCandidate, int, PlaceSort, int]] = []

    def search_anchors(self, *, near: str, city: str | None) -> tuple[AnchorCandidate, ...]:
        self.anchor_calls.append((near, city))
        if self.raises:
            raise self.raises
        return self.anchors

    def get_anchor(self, *, ref: str) -> AnchorCandidate | None:
        self.ref_calls.append(ref)
        if self.raises:
            raise self.raises
        return self.refs.get(ref)

    def search_nearby(
        self,
        *,
        query: str,
        anchor: AnchorCandidate,
        radius_meters: int,
        sort: PlaceSort,
        limit: int,
    ) -> tuple[NearbyPlace, ...]:
        self.nearby_calls.append((query, anchor, radius_meters, sort, limit))
        if self.raises:
            raise self.raises
        return self.places


def _payload(tool: NearbyPlaceSearchTool, args: dict[str, object]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(tool.invoke(args).text)
    return payload


def test_single_anchor_returns_range_and_only_available_fields() -> None:
    provider = _StubProvider(
        anchors=(_anchor(),),
        places=(
            NearbyPlace(
                name="证件照相馆",
                distance_meters=320,
                address="广东省深圳市福田区福强路2号",
            ),
        ),
    )
    tool = NearbyPlaceSearchTool(provider)

    result = tool.invoke(
        {
            "query": "照相馆",
            "near": "福民银行",
            "city": "深圳",
            "sort": "distance",
        }
    )

    assert result.is_error is False
    payload = json.loads(result.text)
    assert payload["status"] == "success"
    assert "trust" not in payload
    assert payload["search_area"] == {
        "center_name": "福民银行",
        "center_address": "广东省深圳市福田区福民路1号",
        "radius_meters": 5000,
        "description": "以福民银行为中心 5 公里内",
    }
    assert payload["places"] == [
        {
            "name": "证件照相馆",
            "distance_meters": 320,
            "address": "广东省深圳市福田区福强路2号",
        }
    ]
    assert "telephone" not in payload["places"][0]
    assert "opening_hours" not in payload["places"][0]
    assert "address 完整原文" in payload["evidence_note"]
    assert "包括楼层和房号" in payload["evidence_note"]
    assert "地点服务标注的营业时间" in payload["evidence_note"]
    assert "不能据此断言当前一定营业或已经关门" in payload["evidence_note"]
    assert provider.anchor_calls == [("福民银行", "深圳")]
    assert provider.nearby_calls[0][2:] == (5000, "distance", 5)
    assert result.meta["provider"] == "stub"


def test_multiple_same_city_candidates_require_clarification() -> None:
    provider = _StubProvider(
        anchors=(
            _anchor(ref="a", name="福民银行福田支行", address="深圳市福田区A路1号"),
            _anchor(ref="b", name="福民银行南山支行", address="深圳市南山区B路2号"),
        )
    )

    payload = _payload(
        NearbyPlaceSearchTool(provider),
        {"query": "照相馆", "near": "福民银行", "city": "深圳"},
    )

    assert payload["status"] == "needs_clarification"
    assert [item["anchor_ref"] for item in payload["candidates"]] == ["a", "b"]
    assert "longitude" not in payload["candidates"][0]
    assert provider.nearby_calls == []


def test_truncated_candidates_require_explicit_disclosure() -> None:
    provider = _StubProvider(
        anchors=tuple(
            _anchor(
                ref=f"anchor-{index}",
                name=f"福民支行{index}",
                address=f"深圳市福田区福民路{index}号",
            )
            for index in range(1, 8)
        )
    )

    payload = _payload(
        NearbyPlaceSearchTool(provider),
        {"query": "照相馆", "near": "福民银行", "city": "深圳"},
    )

    assert len(payload["candidates"]) == 5
    assert payload["additional_candidate_count"] == 2
    assert "只展示前 5 个候选" in payload["candidate_note"]
    assert "不能称为全部候选" in payload["candidate_note"]


def test_one_exact_name_among_other_candidates_is_selected() -> None:
    exact = _anchor(ref="exact")
    provider = _StubProvider(
        anchors=(
            exact,
            _anchor(ref="branch", name="福民银行南山支行", address="深圳市南山区B路2号"),
        ),
        places=(),
    )

    payload = _payload(
        NearbyPlaceSearchTool(provider),
        {"query": "药店", "near": " 福民银行 ", "city": "深圳"},
    )

    assert payload["status"] == "no_place_results"
    assert provider.nearby_calls[0][1] == exact


def test_anchor_ref_rehydrates_selected_candidate_without_text_search() -> None:
    selected = _anchor(ref="selected", name="福民银行南山支行")
    provider = _StubProvider(refs={"selected": selected}, places=())

    payload = _payload(
        NearbyPlaceSearchTool(provider),
        {
            "query": "照相馆",
            "near": "福民银行",
            "city": "深圳",
            "anchor_ref": "selected",
            "radius_meters": 1200,
        },
    )

    assert payload["status"] == "no_place_results"
    assert payload["search_area"]["radius_meters"] == 1200
    assert payload["search_area"]["description"].endswith("1200 米内")
    assert provider.ref_calls == ["selected"]
    assert provider.anchor_calls == []


def test_invalid_anchor_ref_requires_reconfirmation() -> None:
    provider = _StubProvider(anchors=(_anchor(ref="new"),))

    payload = _payload(
        NearbyPlaceSearchTool(provider),
        {"query": "照相馆", "near": "福民银行", "anchor_ref": "old"},
    )

    assert payload["status"] == "needs_clarification"
    assert payload["candidates"][0]["anchor_ref"] == "new"
    assert "已失效" in payload["reason"]
    assert provider.nearby_calls == []


def test_invalid_anchor_ref_without_new_candidates_requests_more_detail() -> None:
    payload = _payload(
        NearbyPlaceSearchTool(_StubProvider()),
        {"query": "照相馆", "near": "福民银行", "anchor_ref": "old"},
    )

    assert payload["status"] == "no_anchor_results"
    assert "已失效" in payload["reason"]
    assert "完整" in payload["next_action"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"query": "", "near": "福民银行"}, "query"),
        ({"query": "药店", "near": ""}, "near"),
        ({"query": "药店", "near": "福民银行", "radius_meters": 99}, "radius"),
        ({"query": "药店", "near": "福民银行", "limit": 11}, "limit"),
        ({"query": "药店", "near": "福民银行", "sort": "nearest"}, "sort"),
    ],
)
def test_invalid_arguments_fail_before_provider_call(args: dict[str, object], message: str) -> None:
    provider = _StubProvider(anchors=(_anchor(),))
    result = NearbyPlaceSearchTool(provider).invoke(args)

    assert result.is_error is True
    payload = json.loads(result.text)
    assert payload["status"] == "invalid_arguments"
    assert message in payload["message"]
    assert provider.anchor_calls == []


def test_no_anchor_results_suggests_more_specific_location() -> None:
    payload = _payload(
        NearbyPlaceSearchTool(_StubProvider()),
        {"query": "药店", "near": "某商场"},
    )
    assert payload["status"] == "no_anchor_results"
    assert "完整" in payload["next_action"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NearbyPlaceAuthenticationError("secret"), "configuration_required"),
        (NearbyPlaceTemporaryError("timeout"), "temporary_failure"),
    ],
)
def test_provider_errors_are_sanitized(error: BaseException, status: str) -> None:
    result = NearbyPlaceSearchTool(_StubProvider(raises=error)).invoke(
        {"query": "药店", "near": "福民银行"}
    )

    assert result.is_error is True
    payload = json.loads(result.text)
    assert payload["status"] == status
    assert "secret" not in result.text
    assert "timeout" not in result.text


def test_description_requires_range_disclosure_and_safe_web_collaboration() -> None:
    description = NearbyPlaceSearchTool.description
    assert "实际搜索半径" in description
    assert "可继续用 web_search" in description
    assert "不能用 web_search 假装" in description
    assert "needs_clarification" in description
    assert "additional_candidate_count" in description
    assert "完整地址" in description
    assert "地点服务标注" in description
    assert "不能直接断言正在营业或一定已经关门" in description


def test_tool_uses_065_bounded_parallel_read_runtime() -> None:
    assert NearbyPlaceSearchTool.execution_kind == "parallel_read"
    assert NearbyPlaceSearchTool.timeout_seconds == 30.0


def test_provider_protocol_shape_is_runtime_checkable() -> None:
    assert isinstance(_StubProvider(), NearbyPlaceProvider)
