from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("run.py")
SPEC = importlib.util.spec_from_file_location("agent_friend_check_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
STAGES = MODULE.STAGES
resolve_stages = MODULE.resolve_stages


def test_ordinary_terminal_defaults_to_full_gate() -> None:
    assert resolve_stages(None, is_codex=False, allow_long_running=False) == STAGES


def test_codex_requires_bounded_stage_by_default() -> None:
    with pytest.raises(ValueError, match="codex_requires_bounded_stages"):
        resolve_stages(None, is_codex=True, allow_long_running=False)


def test_codex_can_run_each_bounded_stage() -> None:
    for stage in STAGES:
        assert resolve_stages(stage, is_codex=True, allow_long_running=False) == (stage,)


def test_codex_full_gate_requires_explicit_override() -> None:
    assert resolve_stages("all", is_codex=True, allow_long_running=True) == STAGES


def test_frontend_stage_fails_closed_without_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(MODULE, "ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        MODULE._run_frontend()

    assert exc_info.value.code == 2
    assert "frontend dependencies are not installed" in capsys.readouterr().err


def test_tracked_shell_entrypoints_are_executable() -> None:
    # On a POSIX checkout exercise the pending worktree modes too. On Windows
    # only the Git mode predicts what a fresh POSIX checkout will receive.
    entries = (
        subprocess.check_output(
            ["git", "ls-files", "--stage", "-z", "scripts/*/run.sh"], cwd=MODULE.ROOT
        )
        .decode()
        .split("\0")
    )
    assert entries[0], "no tracked shell entrypoints"
    missing = []
    for entry in filter(None, entries):
        metadata, path = entry.split("\t", 1)
        executable = (
            metadata.split()[0] == "100755"
            if os.name == "nt"
            else os.access(MODULE.ROOT / path, os.X_OK)
        )
        if not executable:
            missing.append(path)
    assert not missing, missing


@pytest.mark.parametrize(
    ("path", "platforms", "package", "handoff"),
    [
        ("frontend/src-tauri/src/power_policy/platform/macos.rs", ["macos"], False, []),
        (
            "frontend/src-tauri/src/runtime_services/source.rs",
            ["macos", "windows"],
            False,
            ["windows"],
        ),
        ("frontend/src-tauri/src/runtime_services/supervisor.rs", ["macos"], True, ["windows"]),
    ],
)
def test_verification_plan_uses_behavior_targets_and_installer_floor(
    path: str,
    platforms: list[str],
    package: bool,
    handoff: list[str],
) -> None:
    sys.path.insert(0, str(MODULE_PATH.parent))
    try:
        from platform_plan import verification_plan
    finally:
        sys.path.pop(0)
    plan = verification_plan({path}, platforms, "macos")
    assert plan["windowsPackageRequired"] is package
    assert plan["handoffTo"] == handoff
