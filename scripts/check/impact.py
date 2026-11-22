"""Select local check stages; semantic contract review can widen this path-based floor."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

STAGES = ("backend", "frontend", "rust")
BACKEND_ROOTS = (
    "agent/",
    "agent_bridge/",
    "agent_eval/",
    "memory/",
    "memory_eval/",
    "llm_providers/",
    "tools/",
    "voice_bridge/",
)
BACKEND_CONFIG = {"pyproject.toml", "uv.lock", ".python-version"}
DOCUMENT_FILES = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
}
DOCUMENT_SUFFIXES = {".md", ".mdc", ".png", ".jpg", ".jpeg", ".svg", ".webp"}
BRIDGE_CONTRACT_PREFIXES = (
    "frontend/src/services/api/",
    "agent_bridge/src/agent_bridge/routes/",
    "agent_bridge/src/agent_bridge/protocols/ag_ui/",
    "agent_bridge/src/agent_bridge/push/",
)
BRIDGE_CONTRACT_FILES = {
    "frontend/src/services/http.ts",
    "frontend/src/services/stream.ts",
    "frontend/src/types/meta.ts",
    "frontend/src/types/push.ts",
    "frontend/src/types/run.ts",
    "frontend/src/types/notification.ts",
    "frontend/src/types/search.ts",
    "agent_bridge/src/agent_bridge/session_models.py",
}
NATIVE_CONTRACT_PREFIXES = (
    "frontend/src/lib/settings/",
    "frontend/src/lib/persistence/",
    "frontend/src/lib/permissions/",
    "frontend/src/lib/power-policy/",
    "frontend/src/lib/runtime-config/",
    "frontend/src/lib/dataMigration/",
)
NATIVE_CONTRACT_FILES = {
    "frontend/src-tauri/src/lib.rs",
    "frontend/src-tauri/src/settings.rs",
    "frontend/src-tauri/tauri.conf.json",
    "frontend/src/services/runtime.ts",
    "frontend/src/services/autostart.ts",
}


def stages_for_path(path: str) -> tuple[tuple[str, ...], str]:
    # More specific native/contracts must precede frontend/ and backend roots.
    if path.startswith(
        (
            "shared/",
            "runtime_config/",
            "scripts/check/",
            "scripts/_lib/",
            "scripts/package/",
            ".github/workflows/",
        )
    ):
        return STAGES, "shared contract or build/check infrastructure"
    if path in BRIDGE_CONTRACT_FILES or path.startswith(BRIDGE_CONTRACT_PREFIXES):
        return ("backend", "frontend"), "bridge contract and frontend consumer"
    if path in NATIVE_CONTRACT_FILES or path.startswith(NATIVE_CONTRACT_PREFIXES):
        return ("frontend", "rust"), "desktop contract and frontend consumer"
    if path.startswith(("frontend/src-tauri/", "scripts/rust-check/")):
        return ("rust",), "native desktop code or Rust checks"
    if path.startswith(("frontend/", "scripts/frontend/")):
        return ("frontend",), "frontend code, dependencies or checks"
    if path in BACKEND_CONFIG or path.startswith(
        (*BACKEND_ROOTS, "scripts/lint/", "scripts/typecheck/", "scripts/test/")
    ):
        return ("backend",), "Python code, dependencies or checks"
    if (
        path in DOCUMENT_FILES
        or (
            path.startswith(("docs/", ".cursor/", ".claude/", ".github/assets/"))
            and PurePosixPath(path).suffix in DOCUMENT_SUFFIXES
        )
        or path == "scripts/README.md"
    ):
        return (), "documentation/guidance; applicable content and adapter checks still required"
    return STAGES, "unclassified path; run all local stages"


def stage_plan(paths: set[str]) -> dict[str, Any]:
    impacts = []
    selected: set[str] = set()
    for path in sorted(paths):
        stages, reason = stages_for_path(path)
        selected.update(stages)
        impacts.append({"path": path, "stages": list(stages), "reason": reason})
    return {
        "localStages": [stage for stage in STAGES if stage in selected],
        "changedPaths": sorted(paths),
        "pathImpacts": impacts,
    }
