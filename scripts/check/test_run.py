from __future__ import annotations

import importlib.util
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
