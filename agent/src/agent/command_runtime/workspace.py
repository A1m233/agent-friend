"""Session-scoped managed command workspace paths."""

from __future__ import annotations

import hashlib
from pathlib import Path


def session_workspace_path(root: Path, session_id: str) -> Path:
    """Map an opaque logical session id to one stable, portable child directory."""

    if not session_id:
        raise ValueError("session_id 不能为空")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return root.resolve() / f"session-{digest}"
