"""Fail closed when frozen-runtime dependencies change without packaging review."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

FROZEN_RUNTIME_PROJECTS = (
    "agent",
    "agent_bridge",
    "llm_providers",
    "memory",
    "runtime_config",
    "voice_bridge",
)

# Every direct dependency of a frozen runtime project must be classified here.
# Adding a dependency therefore forces an explicit review of package data, hidden
# imports, native libraries, and a frozen capability smoke before packaging can
# proceed. The policy describes the current collection path; it is not an
# instruction to collect every file from every dependency.
REVIEWED_DIRECT_DEPENDENCIES = {
    "ag-ui-protocol": "module-graph",
    "agent": "workspace-collect-all",
    "bilibili-api-python": "spec-collect-all",
    "cryptography": "pyinstaller-hook",
    "fastapi": "module-graph",
    "httpx": "module-graph",
    "jieba": "module-graph",
    "keyring": "spec-backend-collection",
    "litellm": "spec-targeted-data",
    "llm-providers": "workspace-collect-all",
    "lxml": "pyinstaller-hook",
    "mcp": "spec-collect-all",
    "memory": "workspace-collect-all",
    "numpy": "pyinstaller-hook",
    "onnxruntime": "pyinstaller-hook-and-runtime-supplement",
    "openai": "module-graph",
    "platformdirs": "module-graph",
    "portalocker": "module-graph",
    "pydantic": "module-graph",
    "pydantic-settings": "module-graph",
    "pypdf": "module-graph",
    "python-dateutil": "module-graph",
    "python-dotenv": "module-graph",
    "pyyaml": "module-graph",
    "qqbot-agent-sdk": "spec-collect-all",
    "runtime-config": "workspace-collect-all",
    "sqlite-vec": "spec-collect-all",
    "tavily-python": "module-graph",
    "tokenizers": "pyinstaller-hook-and-runtime-supplement",
    "trafilatura": "project-hook",
    "tzdata": "pyinstaller-hook",
    "uvicorn": "spec-collect-all",
    "watchfiles": "agent-collect-all-module-graph-native-extension",
    "websockets": "module-graph",
}

_NAME_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _canonical_dependency_name(requirement: str) -> str:
    match = _NAME_PREFIX.match(requirement.strip())
    if match is None:
        raise RuntimeError(f"cannot parse runtime dependency requirement: {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def frozen_runtime_direct_dependencies(repo_root: Path) -> set[str]:
    dependencies: set[str] = set()
    for project in FROZEN_RUNTIME_PROJECTS:
        pyproject = repo_root / project / "pyproject.toml"
        with pyproject.open("rb") as handle:
            config = tomllib.load(handle)
        for requirement in config["project"].get("dependencies", []):
            dependencies.add(_canonical_dependency_name(str(requirement)))
    return dependencies


def validate_frozen_runtime_dependency_policy(repo_root: Path) -> None:
    actual = frozen_runtime_direct_dependencies(repo_root)
    reviewed = set(REVIEWED_DIRECT_DEPENDENCIES)
    missing = sorted(actual - reviewed)
    stale = sorted(reviewed - actual)
    if not missing and not stale:
        return

    details = []
    if missing:
        details.append(f"unreviewed={','.join(missing)}")
    if stale:
        details.append(f"stale={','.join(stale)}")
    raise RuntimeError(
        "frozen runtime dependency policy drift: "
        + "; ".join(details)
        + ". Review package data, hidden imports, native libraries, and frozen capability smokes "
        "before updating REVIEWED_DIRECT_DEPENDENCIES."
    )
