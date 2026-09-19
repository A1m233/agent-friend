"""Input accounting identity, distinct from request/cache and output identity."""

from typing import Any

from .usage_components import digest

# Unknown settings stay in the identity. Only known output/transport controls are
# removed; message/tool identities still account for every rendered input change.
_NON_INPUT_SETTINGS = frozenset(
    {
        "reasoning_effort",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "temperature",
        "top_p",
        "seed",
        "timeout",
        "stream_timeout",
    }
)


def input_compatibility_scope(identity: dict[str, Any]) -> str:
    normalized = {
        key: value for key, value in identity.items() if key not in {"reasoning", "tools"}
    }
    for field in ("defaults", "settings"):
        value = normalized.get(field)
        if isinstance(value, dict):
            normalized[field] = {
                key: item for key, item in value.items() if key not in _NON_INPUT_SETTINGS
            }
    return digest({"input_identity_version": 4, **normalized})
