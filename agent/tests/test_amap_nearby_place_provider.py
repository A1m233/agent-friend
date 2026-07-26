"""高德附近地点 provider 的 HTTP 契约测试；不访问真实网络。"""

from __future__ import annotations

import httpx
import pytest
from agent.tools.builtin.nearby_place_search import (
    AnchorCandidate,
    NearbyPlaceAuthenticationError,
    NearbyPlaceInvalidRequestError,
    NearbyPlaceQuotaError,
    NearbyPlaceRateLimitError,
    NearbyPlaceTemporaryError,
)
from agent.tools.builtin.nearby_place_search.providers import AmapNearbyPlaceProvider


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(base_url="https://restapi.amap.com", transport=handler)


def _anchor() -> AnchorCandidate:
    return AnchorCandidate(
        ref="B001",
        name="福民银行",
        address="广东省深圳市福田区福民路1号",
        city="深圳市",
        district="福田区",
        longitude=114.0551234,
        latitude=22.5219876,
    )


def test_search_anchors_applies_city_limit_and_filters_invalid_rows() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "infocode": "10000",
                "pois": [
                    {
                        "id": "B001",
                        "name": "福民银行",
                        "location": "114.055123,22.521988",
                        "pname": "广东省",
                        "cityname": "深圳市",
                        "adname": "福田区",
                        "address": "福民路1号",
                    },
                    {"id": "missing-address", "name": "坏数据", "location": "1,2"},
                ],
            },
        )

    with _client(httpx.MockTransport(respond)) as client:
        provider = AmapNearbyPlaceProvider("test-key", client=client)
        anchors = provider.search_anchors(near="福民银行", city="深圳")

    assert len(anchors) == 1
    assert anchors[0].address == "广东省深圳市福田区福民路1号"
    request = seen[0]
    assert request.url.path == "/v5/place/text"
    assert request.url.params["keywords"] == "福民银行"
    assert request.url.params["region"] == "深圳"
    assert request.url.params["city_limit"] == "true"
    assert request.url.params["key"] == "test-key"


def test_search_anchors_without_city_omits_region_params() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "1", "pois": []})

    with _client(httpx.MockTransport(respond)) as client:
        AmapNearbyPlaceProvider("key", client=client).search_anchors(near="福民银行", city=None)

    assert "region" not in seen[0].url.params
    assert "city_limit" not in seen[0].url.params


def test_get_anchor_uses_detail_endpoint() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "id": "B001",
                        "name": "福民银行",
                        "location": "114.055123,22.521988",
                        "pname": "广东省",
                        "cityname": "深圳市",
                        "adname": "福田区",
                        "address": "福民路1号",
                    }
                ],
            },
        )

    with _client(httpx.MockTransport(respond)) as client:
        anchor = AmapNearbyPlaceProvider("key", client=client).get_anchor(ref="B001")

    assert anchor is not None
    assert seen[0].url.path == "/v5/place/detail"
    assert seen[0].url.params["id"] == "B001"


def test_nearby_distance_sort_filters_missing_fields_and_parses_business() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {
                        "name": "较远照相馆",
                        "distance": "900.4",
                        "pname": "广东省",
                        "cityname": "深圳市",
                        "adname": "福田区",
                        "address": "A路1号",
                        "business": {
                            "tel": "0755-12345678",
                            "opentime_week": "周一至周五 09:00-18:00",
                        },
                    },
                    {
                        "name": "最近照相馆",
                        "distance": "120",
                        "address": "深圳市福田区B路2号",
                        "business": {"opentime_today": "09:00-20:00"},
                    },
                    {"name": "无距离", "address": "C路3号"},
                    {"name": "无地址", "distance": "10"},
                ],
            },
        )

    with _client(httpx.MockTransport(respond)) as client:
        places = AmapNearbyPlaceProvider("key", client=client).search_nearby(
            query="照相馆",
            anchor=_anchor(),
            radius_meters=5000,
            sort="distance",
            limit=5,
        )

    assert [place.name for place in places] == ["最近照相馆", "较远照相馆"]
    assert places[0].distance_meters == 120
    assert places[0].telephone is None
    assert places[0].opening_hours == "09:00-20:00"
    assert places[1].telephone == "0755-12345678"
    assert places[1].opening_hours == "周一至周五 09:00-18:00"
    request = seen[0]
    assert request.url.path == "/v5/place/around"
    assert request.url.params["location"] == "114.055123,22.521988"
    assert request.url.params["radius"] == "5000"
    assert request.url.params["sortrule"] == "distance"
    assert request.url.params["show_fields"] == "business"
    assert request.url.params["page_size"] == "25"


def test_relevance_sort_preserves_provider_order_and_applies_limit() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "pois": [
                    {"name": "高相关", "distance": "900", "address": "A路"},
                    {"name": "较近", "distance": "100", "address": "B路"},
                ],
            },
        )

    with _client(httpx.MockTransport(respond)) as client:
        places = AmapNearbyPlaceProvider("key", client=client).search_nearby(
            query="药店",
            anchor=_anchor(),
            radius_meters=1000,
            sort="relevance",
            limit=1,
        )

    assert [place.name for place in places] == ["高相关"]


@pytest.mark.parametrize(
    ("infocode", "error_type"),
    [
        ("10001", NearbyPlaceAuthenticationError),
        ("10003", NearbyPlaceQuotaError),
        ("10014", NearbyPlaceRateLimitError),
        ("20000", NearbyPlaceInvalidRequestError),
        ("10016", NearbyPlaceTemporaryError),
        ("39999", NearbyPlaceTemporaryError),
    ],
)
def test_amap_infocodes_are_classified(infocode: str, error_type: type[BaseException]) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "infocode": infocode, "info": "secret raw error"},
        )

    with _client(httpx.MockTransport(respond)) as client:
        provider = AmapNearbyPlaceProvider("key", client=client)
        with pytest.raises(error_type) as caught:
            provider.search_anchors(near="x", city=None)
    assert "secret raw error" not in str(caught.value)


def test_http_auth_rate_and_bad_json_are_sanitized() -> None:
    cases = [
        (401, NearbyPlaceAuthenticationError),
        (429, NearbyPlaceRateLimitError),
        (503, NearbyPlaceTemporaryError),
    ]
    for status_code, error_type in cases:
        transport = httpx.MockTransport(
            lambda _, current_status=status_code: httpx.Response(
                current_status, text="secret-key-in-body"
            )
        )
        with _client(transport) as client, pytest.raises(error_type):
            AmapNearbyPlaceProvider("key", client=client).search_anchors(near="x", city=None)

    bad_json = httpx.MockTransport(lambda _: httpx.Response(200, text="not-json"))
    with _client(bad_json) as client, pytest.raises(NearbyPlaceTemporaryError):
        AmapNearbyPlaceProvider("key", client=client).search_anchors(near="x", city=None)
