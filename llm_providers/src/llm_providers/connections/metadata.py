"""Best-effort model limits with explicit provenance; never infer capabilities from names."""

from typing import Any

from ..catalog import PROVIDER_DEFINITIONS


def positive_limit(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def model_metadata(name: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw or {}
    for key in ("context_length", "context_window", "max_model_len", "max_input_tokens"):
        if limit := positive_limit(raw.get(key)):
            return {
                "id": name,
                "contextWindow": limit,
                "contextSource": "endpoint",
                "contextLabel": "服务返回 · " + key,
            }
    for key in ("limit", "limits"):
        nested = raw.get(key)
        if isinstance(nested, dict) and (limit := positive_limit(nested.get("context"))):
            return {
                "id": name,
                "contextWindow": limit,
                "contextSource": "endpoint",
                "contextLabel": "服务返回 · " + key + ".context",
            }
    matches = [
        model.context_window
        for provider in PROVIDER_DEFINITIONS.values()
        for model in provider.models
        if model.litellm_model.split("/", 1)[-1] == name
    ]
    if len(set(matches)) == 1:
        return {
            "id": name,
            "contextWindow": matches[0],
            "contextSource": "catalog",
            "contextLabel": "内置目录参考，服务配置可能不同",
        }
    # The bundled SDK catalog is local metadata, not a live request or a server guarantee.
    import litellm

    info = litellm.model_cost.get(name) or litellm.model_cost.get("openai/" + name)
    if isinstance(info, dict) and (limit := positive_limit(info.get("max_input_tokens"))):
        return {
            "id": name,
            "contextWindow": limit,
            "contextSource": "catalog",
            "contextLabel": "SDK 目录参考，服务配置可能不同",
        }
    return {
        "id": name,
        "contextWindow": None,
        "contextSource": None,
        "contextLabel": "未获取到上下文信息",
    }


def discovered_models(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid_model_list")
    result = {}
    for raw in payload["data"]:
        if not isinstance(raw, dict):
            continue
        name = raw.get("id")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 512
            or any(ord(c) < 32 for c in name)
        ):
            continue
        result[name] = model_metadata(name, raw)
        if len(result) >= 500:
            break
    return list(result.values())
