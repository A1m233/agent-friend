"""Plan host checks and platform-specific evidence; do not execute them."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from impact import stage_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))
from install_contract import changed_repository_paths, classify_installer_impact


def verification_plan(paths: set[str], affected: list[str], host: str) -> dict[str, Any]:
    impact = classify_installer_impact(paths)
    targets = set(affected)
    if impact["packageRequired"]:
        targets.add("windows")
    return {
        **stage_plan(paths),
        "host": host,
        "behaviorChecksOn": sorted(set(affected)),
        "handoffTo": sorted(targets - {host}),
        "windowsPackageRequired": impact["packageRequired"],
        "minimumVerificationLevel": impact["minimumVerificationLevel"],
        "packageSensitivePaths": impact["packageSensitivePaths"],
        "fullMatrixPaths": impact["fullMatrixPaths"],
    }


def plan_for_checkout(root: Path, base: str, affected: list[str]) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()

    try:
        base_commit = git("rev-parse", "--verify", f"{base}^{{commit}}")
        head_commit = git("rev-parse", "HEAD")
        ancestor = git("merge-base", head_commit, base_commit)
    except subprocess.CalledProcessError as error:
        raise ValueError(
            f"Cannot resolve check baseline {base!r}; fetch/verify the baseline, "
            "or explicitly run --stage all when a trusted baseline is unavailable."
        ) from error
    if ancestor != base_commit:
        raise ValueError(
            f"HEAD does not contain baseline {base!r} ({base_commit}); "
            "integrate the latest baseline before selecting checks for the merge."
        )
    host = {"Darwin": "macos", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    return {
        "base": base,
        "baseCommit": base_commit,
        "headCommit": head_commit,
        **verification_plan(changed_repository_paths(root, base_commit), affected, host),
    }
