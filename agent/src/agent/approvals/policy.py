"""Read the autonomous companionship authorization policy from a host-owned settings snapshot."""

from typing import Any


def authorization_policy(values: dict[str, Any] | None) -> tuple[str, int]:
    values = values or {}
    mode = values.get("authorizationMode", "auto_review")
    version = values.get("authorizationPolicyVersion", 0)
    return (
        mode if isinstance(mode, str) and mode in {"auto_review", "full_access"} else "auto_review",
        version if type(version) is int and version >= 0 else 0,
    )
