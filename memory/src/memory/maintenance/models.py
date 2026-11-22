"""Domain values for ordinary Semantic maintenance, independent of agent/UI policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

ACTIONS = frozenset({"keep", "pending", "retire", "revoke", "rewrite", "merge"})


class MaintenanceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def fact(raw: Any) -> dict[str, Any]:
    value = dict(raw)
    value.pop("statement_tokens", None)
    value["provenance"] = json.loads(value.get("provenance") or "[]")
    return value


def revision(value: dict[str, Any]) -> str:
    return digest(value)


@dataclass(frozen=True)
class MaintenanceAuthorization:
    """Created by the trusted approval adapter, never deserialized from tool arguments."""

    batch_id: str
    batch_digest: str
    request_id: str
    session_id: str
    decision_source: str
    source_ref: str | None = None
