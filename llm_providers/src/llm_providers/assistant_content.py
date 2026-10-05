"""Provider-neutral, display-only metadata for completed assistant content."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True, repr=False)
class AssistantContentPart:
    content_index: int
    kind: Literal["text", "refusal"]
    start: int
    end: int
    annotations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["annotations"] = list(result["annotations"])
        return result


def content_metadata(parts: tuple[AssistantContentPart, ...]) -> list[dict[str, Any]]:
    return [part.to_dict() for part in parts]


def normalize_annotations(value: Any) -> tuple[dict[str, Any], ...]:
    """Keep citation facts, never opaque provider payloads or executable links."""
    if not isinstance(value, list):
        return ()
    result = []
    for annotation in value:
        if not isinstance(annotation, dict):
            continue
        kind = annotation.get("type")
        if not isinstance(kind, str) or kind not in {
            "url_citation",
            "file_citation",
            "container_file_citation",
            "file_path",
        }:
            result.append({"type": "unavailable"})
            continue
        item: dict[str, Any] = {"type": kind}
        for key in ("url", "title", "filename", "file_id", "container_id"):
            text = annotation.get(key)
            if isinstance(text, str):
                item[key] = text
        for key in ("start_index", "end_index", "index"):
            index = annotation.get(key)
            if type(index) is int and index >= 0:
                item[key] = index
        result.append(item)
    return tuple(result)
