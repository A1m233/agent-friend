"""Locked Xiaohongshu connector artifacts and local runtime path resolution."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import platformdirs

from agent.paths import APP_NAME

XIAOHONGSHU_VERSION = "v2.2.7"
XIAOHONGSHU_COMMIT = "3aacfb3583482424c0170e86e05725df940dd35e"
XIAOHONGSHU_ARTIFACT_DIR_ENV = "AGENT_FRIEND_XIAOHONGSHU_CONNECTOR_DIR"
ArtifactRole = Literal["server", "login"]
ArtifactSource = Literal["release", "source-build"]


@dataclass(frozen=True)
class XiaohongshuArtifact:
    role: ArtifactRole
    system: str
    machine: str
    filename: str
    size: int
    sha256: str
    source: ArtifactSource = "release"

    @property
    def url(self) -> str:
        if self.source != "release":
            raise RuntimeError(f"{self.filename} is produced by the locked source build")
        return (
            "https://github.com/xpzouying/xiaohongshu-mcp/releases/download/"
            f"{XIAOHONGSHU_VERSION}/{self.filename}"
        )


_ARTIFACTS = (
    XiaohongshuArtifact(
        role="login",
        system="darwin",
        machine="arm64",
        filename="xiaohongshu-login-darwin-arm64",
        size=11_698_098,
        sha256="eaaf7d0f2b885fcd8442ec51ee86347275844c28748667259d8d8ada24cf54a0",
    ),
    XiaohongshuArtifact(
        role="server",
        system="darwin",
        machine="arm64",
        filename="xiaohongshu-mcp-darwin-arm64",
        size=16_276_034,
        sha256="88d341f8834033866a69cfe696ffe404645799d41afc174b347a0e4398a1c11c",
    ),
    XiaohongshuArtifact(
        role="login",
        system="linux",
        machine="amd64",
        filename="xiaohongshu-login-linux-amd64",
        size=11_624_632,
        sha256="2614e5e9245d4f45ab64508a0f8a523a4a9b9a6d0a8df503659c866f0aea418f",
    ),
    XiaohongshuArtifact(
        role="server",
        system="linux",
        machine="amd64",
        filename="xiaohongshu-mcp-linux-amd64",
        size=16_224_440,
        sha256="fb8de47b53fa5cbea1da47ad2bb6ec95ae9846d9cd787a98f4b31fd0b9de0e98",
    ),
    XiaohongshuArtifact(
        role="login",
        system="windows",
        machine="amd64",
        filename="xiaohongshu-login-windows-amd64.exe",
        size=14_947_840,
        sha256="8913c8deadd3373648a43ad86c7760e7b03318d8cf0bdf6b350d7a4d82a4bb4d",
        source="source-build",
    ),
    XiaohongshuArtifact(
        role="server",
        system="windows",
        machine="amd64",
        filename="xiaohongshu-mcp-windows-amd64.exe",
        size=21_488_128,
        sha256="099a27a7fbd718aa47af91fac555606b91fc69b4a4a669c2adb581ea3e3393a4",
        source="source-build",
    ),
)


def current_xiaohongshu_artifacts(
    *, system: str | None = None, machine: str | None = None
) -> tuple[XiaohongshuArtifact, XiaohongshuArtifact]:
    normalized_system = (system or platform.system()).lower()
    normalized_machine = _normalized_machine(machine or platform.machine())
    matches = tuple(
        artifact
        for artifact in _ARTIFACTS
        if artifact.system == normalized_system and artifact.machine == normalized_machine
    )
    if len(matches) != 2:
        raise RuntimeError(
            f"xiaohongshu-mcp {XIAOHONGSHU_VERSION} has no locked artifacts for "
            f"{normalized_system}/{normalized_machine}"
        )
    by_role = {artifact.role: artifact for artifact in matches}
    return by_role["server"], by_role["login"]


def default_xiaohongshu_artifact_dir() -> Path:
    override = os.environ.get(XIAOHONGSHU_ARTIFACT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "connectors" / "xiaohongshu"
    return (
        Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
        / "connectors"
        / "xiaohongshu"
        / XIAOHONGSHU_VERSION
    )


def verify_xiaohongshu_artifact(path: Path, artifact: XiaohongshuArtifact) -> bool:
    if not path.is_file() or path.stat().st_size != artifact.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == artifact.sha256


def _normalized_machine(value: str) -> str:
    normalized = value.lower()
    if normalized in {"x86_64", "amd64"}:
        return "amd64"
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    return normalized


__all__ = [
    "XIAOHONGSHU_ARTIFACT_DIR_ENV",
    "XIAOHONGSHU_COMMIT",
    "XIAOHONGSHU_VERSION",
    "XiaohongshuArtifact",
    "current_xiaohongshu_artifacts",
    "default_xiaohongshu_artifact_dir",
    "verify_xiaohongshu_artifact",
]
