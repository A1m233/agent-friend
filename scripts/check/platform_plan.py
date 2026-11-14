"""Plan host checks and platform-specific evidence; do not execute them."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))
from install_contract import changed_repository_paths, classify_installer_impact


def verification_plan(paths: set[str], affected: list[str], host: str) -> dict[str, Any]:
    impact = classify_installer_impact(paths)
    targets = set(affected)
    if impact["packageRequired"]:
        targets.add("windows")
    return {
        "host": host,
        "behaviorChecksOn": sorted(set(affected)),
        "handoffTo": sorted(targets - {host}),
        "windowsPackageRequired": impact["packageRequired"],
        "minimumVerificationLevel": impact["minimumVerificationLevel"],
        "packageSensitivePaths": impact["packageSensitivePaths"],
        "fullMatrixPaths": impact["fullMatrixPaths"],
    }


def plan_for_checkout(root: Path, base: str, affected: list[str]) -> dict[str, Any]:
    host = {"Darwin": "macos", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    return verification_plan(changed_repository_paths(root, base), affected, host)
