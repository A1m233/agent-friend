"""Stable control state, independent of per-invocation build roots."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

from .files import GIB, read_json

CATEGORIES = ("package-cache", "package-build", "dev", "rust-check", "runtime-regression")
DEFAULTS: dict[str, Any] = {
    "schemaVersion": 1,
    "buildRoot": None,
    "budgetBytes": 80 * GIB,
    "lowWaterBytes": 60 * GIB,
    "cacheDays": 7,
    "retentionHours": 48,
    "automaticCategories": [],
}


def control_root() -> Path:
    home = Path.home()
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
            / "agent-friend/build-control"
        )
    if sys.platform == "darwin":
        return home / "Library/Application Support/agent-friend-build"
    return Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "agent-friend-build"


def default_build_root() -> Path:
    if os.name == "nt":
        return control_root().parent / "build"
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/agent-friend/build"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-friend/build"


def load_config(control: Path) -> dict[str, Any]:
    path = control / "config.json"
    result = dict(DEFAULTS) if not path.exists() else read_json(path)
    if set(result) != set(DEFAULTS):
        raise ValueError("invalid build-storage configuration fields")
    for key in ("budgetBytes", "lowWaterBytes", "cacheDays", "retentionHours"):
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"invalid build-storage configuration: {key}")
    if result["buildRoot"] is not None and not isinstance(result["buildRoot"], str):
        raise ValueError("buildRoot must be a path or null")
    categories = result["automaticCategories"]
    if not isinstance(categories, list) or any(c not in CATEGORIES for c in categories):
        raise ValueError("invalid automatic cleanup scope")
    return result
