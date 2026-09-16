"""Build-owned native application launcher; never resolve helpers from PATH at runtime."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def stage_application_launcher(
    *,
    repo_root: Path,
    target_dir: Path,
    destination: Path,
    identity: dict[str, object],
    env: dict[str, str],
) -> Path:
    build_env = dict(env)
    build_env["AGENT_FRIEND_BUILD_IDENTITY_JSON"] = json.dumps(
        identity, sort_keys=True, separators=(",", ":")
    )
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--bin",
            "agent-friend-app-launcher",
            "--manifest-path",
            str(repo_root / "frontend/src-tauri/Cargo.toml"),
            "--target-dir",
            str(target_dir),
        ],
        cwd=repo_root,
        env=build_env,
        check=True,
    )
    source = target_dir / "release/agent-friend-app-launcher.exe"
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / source.name
    shutil.copy2(source, executable)
    actual = json.loads(subprocess.check_output([str(executable), "--identity"], timeout=5))
    if (
        json.loads(actual.get("build_identity", "")) != identity
        or actual.get("protocol_version") != 1
    ):
        raise RuntimeError("native application launcher identity mismatch")
    executable.with_suffix(".identity.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "identity": actual,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return executable


def build_launch_fixture(repo_root: Path, directory: Path) -> Path:
    """A short-lived, build-owned target for the frozen capability smoke; never shipped."""
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "desktop-fixture.exe"
    subprocess.run(
        ["rustc", str(repo_root / "scripts/desktop-application/fixture.rs"), "-o", str(executable)],
        cwd=repo_root,
        check=True,
    )
    return executable
