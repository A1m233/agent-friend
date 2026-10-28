"""Prove a receipt-less legacy Cargo cache's source using bounded dep-info records."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .files import no_links


def legacy_identity(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()[:12]


def cargo_evidence(target: Path, repo_id: str) -> dict[str, Any]:
    workspace = target.parent
    if (
        target.name != "build-cache"
        or workspace.parent.parent.name != "work"
        or workspace.parent.name != repo_id
    ):
        raise ValueError("Cargo evidence requires exact work/repo/worktree/build-cache layout")
    if not re.fullmatch(r"[0-9a-f]{12}", workspace.name):
        raise ValueError("invalid legacy worktree identity")
    # This fallback owns only a Cargo target, not arbitrary package stages or Python environments.
    if {p.name for p in target.iterdir()} != {"cargo-target"}:
        raise ValueError("receipt-less adoption requires a Cargo-only build-cache")
    cargo = no_links(target / "cargo-target")
    tag = no_links(cargo / "CACHEDIR.TAG")
    if tag.stat().st_size > 4096 or not tag.read_text(encoding="utf-8").startswith(
        "Signature: 8a477f597d28d172789f06886806bc55"
    ):
        raise ValueError("Cargo cache signature missing")
    info = no_links(cargo / ".rustc_info.json")
    if info.stat().st_size > 1024 * 1024 or not isinstance(
        json.loads(info.read_text(encoding="utf-8")), dict
    ):
        raise ValueError("invalid Rust compiler cache metadata")
    candidates: set[Path] = set()
    for profile in ("debug", "release"):
        for folder in (cargo / profile, cargo / profile / "deps"):
            no_links(folder)
            for pattern in ("app*.d", "agent-friend-app-launcher*.d"):
                candidates.update(folder.glob(pattern))
    if len(candidates) > 256:
        raise ValueError("too many dep-info records; explicit investigation required")
    sources: set[Path] = set()
    witnesses = []
    total = 0
    for file in sorted(candidates):
        no_links(file)
        size = file.stat().st_size
        total += size
        if size > 4 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise ValueError("dep-info evidence exceeds bounded read limit")
        raw = file.read_bytes()
        # Rust dep-info can contain doubled backslashes and Make-escaped spaces/drive colons.
        text = raw.decode("utf-8").replace("\\ ", "\x00").replace("\\:", ":")
        text = text.replace("\\\\", "\\").replace("\\", "/")
        matches = re.findall(r"(?<!\S)((?:[A-Za-z]:/|/)[^\s]*?)/frontend/src-tauri/", text)
        for manifest in re.findall(
            r"^# env-dep:CARGO_MANIFEST_DIR=(.+)$", text, flags=re.MULTILINE
        ):
            manifest = manifest.rstrip("\r")
            suffix = "/frontend/src-tauri"
            if manifest.endswith(suffix):
                matches.append(manifest[: -len(suffix)])
        found = set()
        for match in matches:
            source = Path(match.replace("\x00", " "))
            if source.is_absolute():
                found.add(source.resolve())
        if found:
            sources.update(found)
            witnesses.append(
                {"path": str(file), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": size}
            )
    if len(sources) != 1:
        raise ValueError("dep-info source is missing or ambiguous")
    source = sources.pop()
    if legacy_identity(source) != workspace.name:
        raise ValueError("dep-info source does not match legacy worktree identity")
    return {
        "kind": "cargo-dep-info",
        "repoRoot": str(source),
        "repoId": repo_id,
        "worktreeId": workspace.name,
        "witnesses": witnesses,
        "compilerMetadataSha256": hashlib.sha256(info.read_bytes()).hexdigest(),
    }
