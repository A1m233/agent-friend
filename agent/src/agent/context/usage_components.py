"""Content identities for measured requests, independent of their outer ordering.

Only digests leave this module in persisted measurements. Occurrences are kept
separately: another copy of the same content is another input cost.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def components(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> list[tuple[str, dict[str, Any]]]:
    """Keep role, call identity and field domain when splitting countable content."""
    result: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        header = {
            key: value
            for key, value in message.items()
            if key not in {"content", "tool_calls", "reasoning_content", "reasoning_details"}
        }
        # A header is a separate component so empty messages and call IDs cannot
        # silently disappear. The established estimator charges content only.
        result.append((digest(["header", header]), {**header, "content": ""}))
        for field in ("content", "tool_calls", "reasoning_content", "reasoning_details"):
            value = message.get(field)
            if not value:
                continue
            # Reasoning details must stay together for their response usage bound.
            values = value if isinstance(value, list) and field != "reasoning_details" else [value]
            for part in values:
                payload = (
                    [part] if isinstance(value, list) and field != "reasoning_details" else part
                )
                result.append(
                    (digest(["message", header, field, payload]), {**header, field: payload})
                )
    for tool in tools or []:
        result.append((digest(["tool", tool]), {"_tool_schema": tool}))
    return result


class ComponentCache:
    """Memoize component identities only within one frozen candidate search.

    Keep source objects alive: object IDs alone can be reused after collection.
    Occurrences are never deduplicated in the returned list.
    """

    def __init__(self) -> None:
        self._messages: dict[int, tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]] = {}
        self._tools: dict[int, tuple[dict[str, Any], tuple[str, dict[str, Any]]]] = {}

    def components(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> list[tuple[str, dict[str, Any]]]:
        result = []
        for message in messages:
            key = id(message)
            if key not in self._messages:
                self._messages[key] = (message, components([message], None))
            result.extend(self._messages[key][1])
        for tool in tools or []:
            key = id(tool)
            if key not in self._tools:
                self._tools[key] = (tool, components([], [tool])[0])
            result.append(self._tools[key][1])
        return result


def valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
