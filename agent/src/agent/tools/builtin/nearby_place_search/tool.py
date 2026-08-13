"""面向用户意图的附近地点搜索工具。"""

from __future__ import annotations

import json
import time
import unicodedata
from typing import Any, ClassVar

from ...protocol import ToolResult
from .models import (
    AnchorCandidate,
    NearbyPlaceAuthenticationError,
    NearbyPlaceInvalidRequestError,
    NearbyPlaceProvider,
    NearbyPlaceQuotaError,
    NearbyPlaceRateLimitError,
    NearbyPlaceTemporaryError,
    PlaceSort,
)


class NearbyPlaceSearchTool:
    """把锚点解析、歧义澄清和附近 POI 查询编排成一把工具。"""

    name: ClassVar[str] = "nearby_place_search"
    execution_kind: ClassVar[str] = "parallel_read"
    timeout_seconds: ClassVar[float] = 30.0
    description: ClassVar[str] = (
        "以用户给出的地点、地标或地址为锚点，查找指定半径内的地点。"
        "工具内部会解析锚点并完成附近 POI 查询。\n\n"
        "**何时使用**：\n"
        "- 用户问『某地附近的药店/照相馆/餐厅』或『离某地最近的某类地点』\n"
        "- 回答需要可靠的附近性、距离或地理排序事实\n\n"
        "**何时不要使用**：\n"
        "- 用户没有提供地点锚点，或要求设备当前位置、导航、路线、天气\n"
        "- 用户只要官网、预约、近期公告等网页信息；这类信息使用网页搜索\n\n"
        "**参数与澄清**：\n"
        "- query 是目标地点类别，near 是用户给出的锚点，city 只在用户已提供时填写\n"
        "- 用户明确说『最近』时 sort=distance；普通『附近』用 relevance\n"
        "- 默认搜索半径为 5000 米；用户指定范围时换算为 radius_meters\n"
        "- status=needs_clarification 时必须把候选交给用户选择；随后原样传回对应 anchor_ref，不能静默猜测或自行构造 ref\n"
        "- 澄清结果含 additional_candidate_count 时，必须说明当前只展示了部分候选以及尚有多少个；不能把截断列表说成全部结果\n\n"
        "**证据与协作边界**：\n"
        "- 最终回答必须说明确认后的锚点和实际搜索半径，不能只含糊说『附近』\n"
        "- 最终回答列出的每个地点都必须逐项保留工具返回的完整地址，包括楼层和房号；不能只写商场、小区或大厦名\n"
        "- 距离、地址和排序只引用本工具实际返回的字段；电话和营业时间缺失时不要补写\n"
        "- opening_hours 只是地点服务标注的参考时段，不代表当前实时营业；应明确说『地点服务标注』，需要时建议出发前电话确认，不能直接断言正在营业或一定已经关门\n"
        "- 成功后可继续用 web_search 补充官网、预约或近期网页信息，但网页结果不能替代或改写距离事实\n"
        "- 失败、无结果或未配置时，如实说明未完成地点查询；不能用 web_search 假装找到了附近或最近地点\n"
        "- 不要向用户暴露 provider、API、SDK、鉴权、限流或内部错误码"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查找的地点名称或类别，例如『照相馆』『药店』。",
            },
            "near": {
                "type": "string",
                "description": "用户提供的锚点地点、地标或地址。",
            },
            "city": {
                "type": "string",
                "description": "用户明确提供的城市；只缩小候选范围，不保证锚点唯一。",
            },
            "anchor_ref": {
                "type": "string",
                "description": "上次澄清结果中的不透明候选引用，只能原样复用。",
            },
            "radius_meters": {
                "type": "integer",
                "minimum": 100,
                "maximum": 50000,
                "default": 5000,
                "description": "圆形搜索半径，单位米。",
            },
            "sort": {
                "type": "string",
                "enum": ["distance", "relevance"],
                "default": "relevance",
                "description": "『最近』用 distance；普通『附近』用 relevance。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "最多返回多少个地点。",
            },
        },
        "required": ["query", "near"],
        "additionalProperties": False,
    }

    def __init__(self, provider: NearbyPlaceProvider) -> None:
        self._provider = provider

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        validated = _validate_args(args)
        if isinstance(validated, ToolResult):
            return validated
        query, near, city, anchor_ref, radius_meters, sort, limit = validated
        started = time.monotonic()
        try:
            if anchor_ref:
                anchor = self._provider.get_anchor(ref=anchor_ref)
                if anchor is None:
                    candidates = self._provider.search_anchors(near=near, city=city)
                    if not candidates:
                        return _json_result(
                            {
                                "status": "no_anchor_results",
                                "query": query,
                                "near": near,
                                "city": city,
                                "reason": "之前选择的锚点已失效，且当前未找到新的候选。",
                                "next_action": "请用户补充城市、行政区、道路或更完整的锚点地址。",
                            }
                        )
                    return _clarification_result(
                        query=query,
                        near=near,
                        city=city,
                        candidates=candidates,
                        reason="之前选择的锚点已失效，请重新确认。",
                    )
            else:
                candidates = self._provider.search_anchors(near=near, city=city)
                anchor = _unique_anchor(near, candidates)
                if anchor is None:
                    if not candidates:
                        return _json_result(
                            {
                                "status": "no_anchor_results",
                                "query": query,
                                "near": near,
                                "city": city,
                                "next_action": "请用户补充城市、行政区、道路或更完整的锚点地址。",
                            }
                        )
                    return _clarification_result(
                        query=query,
                        near=near,
                        city=city,
                        candidates=candidates,
                    )

            places = self._provider.search_nearby(
                query=query,
                anchor=anchor,
                radius_meters=radius_meters,
                sort=sort,
                limit=limit,
            )
        except NearbyPlaceAuthenticationError:
            return _failure(
                "configuration_required",
                "地点查询暂不可用：请在现有设置页配置地点服务凭据并重启服务。",
            )
        except NearbyPlaceQuotaError:
            return _failure(
                "quota_exhausted",
                "地点查询额度已用完，当前无法可靠完成附近或最近地点查询。",
            )
        except NearbyPlaceRateLimitError:
            return _failure(
                "rate_limited",
                "地点查询请求过于频繁，请稍后再试。",
            )
        except NearbyPlaceInvalidRequestError:
            return _failure(
                "invalid_request",
                "地点服务无法处理这次查询，请换更明确的地点或关键词。",
            )
        except NearbyPlaceTemporaryError:
            return _failure(
                "temporary_failure",
                "地点查询暂时失败，请稍后重试；不要用网页搜索替代距离结论。",
            )

        search_area = _search_area(anchor, radius_meters)
        if not places:
            return _json_result(
                {
                    "status": "no_place_results",
                    "query": query,
                    "sort": sort,
                    "search_area": search_area,
                    "places": [],
                    "next_action": "可以询问用户是否扩大搜索半径或更换目标关键词。",
                },
                meta=_meta(self._provider.name, started, 0),
            )
        payload_places: list[dict[str, Any]] = []
        for place in places:
            item: dict[str, Any] = {
                "name": place.name,
                "distance_meters": place.distance_meters,
                "address": place.address,
            }
            if place.telephone:
                item["telephone"] = place.telephone
            if place.opening_hours:
                item["opening_hours"] = place.opening_hours
            payload_places.append(item)
        return _json_result(
            {
                "status": "success",
                "query": query,
                "sort": sort,
                "search_area": search_area,
                "places": payload_places,
                "evidence_note": (
                    "距离和地址来自本次地点查询。最终回答必须说明锚点和搜索半径，"
                    "并为列出的每个地点保留 address 完整原文（包括楼层和房号），不能只写商场、小区或大厦名。"
                    "电话、营业信息仅在字段存在时引用；opening_hours 应表述为『地点服务标注的营业时间』，"
                    "它不是实时营业状态，不能据此断言当前一定营业或已经关门，需要时建议出发前电话确认。"
                ),
            },
            meta=_meta(self._provider.name, started, len(places)),
        )


def _validate_args(
    args: dict[str, Any],
) -> tuple[str, str, str | None, str | None, int, PlaceSort, int] | ToolResult:
    query = args.get("query")
    near = args.get("near")
    if not isinstance(query, str) or not query.strip():
        return _failure("invalid_arguments", "query 必须是非空地点名称或类别。")
    if not isinstance(near, str) or not near.strip():
        return _failure("invalid_arguments", "near 必须是用户提供的非空地点或地址。")
    city_value = args.get("city")
    if city_value is not None and not isinstance(city_value, str):
        return _failure("invalid_arguments", "city 必须是字符串。")
    ref_value = args.get("anchor_ref")
    if ref_value is not None and (not isinstance(ref_value, str) or not ref_value.strip()):
        return _failure("invalid_arguments", "anchor_ref 必须原样使用有效候选引用。")
    radius = args.get("radius_meters", 5000)
    if isinstance(radius, bool) or not isinstance(radius, int) or not 100 <= radius <= 50000:
        return _failure("invalid_arguments", "radius_meters 必须在 100 到 50000 之间。")
    sort_value = args.get("sort", "relevance")
    if sort_value not in {"distance", "relevance"}:
        return _failure("invalid_arguments", "sort 只能是 distance 或 relevance。")
    limit = args.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        return _failure("invalid_arguments", "limit 必须在 1 到 10 之间。")
    city = city_value.strip() if isinstance(city_value, str) and city_value.strip() else None
    anchor_ref = ref_value.strip() if isinstance(ref_value, str) else None
    return query.strip(), near.strip(), city, anchor_ref, radius, sort_value, limit


def _unique_anchor(near: str, candidates: tuple[AnchorCandidate, ...]) -> AnchorCandidate | None:
    if len(candidates) == 1:
        return candidates[0]
    normalized_near = _normalize(near)
    exact = [
        candidate
        for candidate in candidates
        if normalized_near in {_normalize(candidate.name), _normalize(candidate.address)}
    ]
    return exact[0] if len(exact) == 1 else None


def _normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _clarification_result(
    *,
    query: str,
    near: str,
    city: str | None,
    candidates: tuple[AnchorCandidate, ...],
    reason: str | None = None,
) -> ToolResult:
    payload: dict[str, Any] = {
        "status": "needs_clarification",
        "query": query,
        "near": near,
        "city": city,
        "candidates": [
            {
                "anchor_ref": candidate.ref,
                "name": candidate.name,
                "address": candidate.address,
                **({"city": candidate.city} if candidate.city else {}),
                **({"district": candidate.district} if candidate.district else {}),
            }
            for candidate in candidates[:5]
        ],
        "next_action": "请让用户从当前展示的候选中选择一个锚点，再原样传回对应 anchor_ref。",
    }
    if reason:
        payload["reason"] = reason
    if len(candidates) > 5:
        payload["additional_candidate_count"] = len(candidates) - 5
        payload["candidate_note"] = (
            f"当前只展示前 5 个候选，另有 {len(candidates) - 5} 个未展示；"
            "向用户澄清时必须说明列表已截断，不能称为全部候选。"
        )
    return _json_result(payload)


def _search_area(anchor: AnchorCandidate, radius_meters: int) -> dict[str, Any]:
    distance = (
        f"{radius_meters // 1000} 公里" if radius_meters % 1000 == 0 else f"{radius_meters} 米"
    )
    return {
        "center_name": anchor.name,
        "center_address": anchor.address,
        "radius_meters": radius_meters,
        "description": f"以{anchor.name}为中心 {distance}内",
    }


def _failure(status: str, message: str) -> ToolResult:
    return _json_result(
        {"status": status, "message": message},
        is_error=True,
    )


def _json_result(
    payload: dict[str, Any],
    *,
    is_error: bool = False,
    meta: dict[str, Any] | None = None,
) -> ToolResult:
    body = {
        "source_type": "nearby_place_search",
        **payload,
    }
    return ToolResult(
        text=json.dumps(body, ensure_ascii=False, indent=2),
        is_error=is_error,
        meta=meta or {},
    )


def _meta(provider: str, started: float, result_count: int) -> dict[str, Any]:
    return {
        "provider": provider,
        "duration_seconds": time.monotonic() - started,
        "result_count": result_count,
    }
