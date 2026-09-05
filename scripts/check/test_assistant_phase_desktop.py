from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agent import JsonlSessionStore


def test_desktop_fixture_is_synthetic_isolated_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = Path(__file__).parents[1] / "assistant-phase-desktop/run.py"
    spec = importlib.util.spec_from_file_location("phase_desktop_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "sessions_dir", lambda: tmp_path / "sessions")
    output = tmp_path / "fixture.json"
    module.seed(output)
    fixture = json.loads(output.read_text(encoding="utf-8"))
    session = JsonlSessionStore(tmp_path / "sessions").load(fixture["session_id"])
    assert fixture["synthetic"]
    assert session.current_model == "kimi/kimi-k3"
    assert len([ev for ev in session.active_events if ev.type == "user_message"]) == 3
    assert len([ev for ev in session.active_events if ev.type == "assistant_message"]) == 6
    terminals = [ev for ev in session.active_events if ev.type == "run_terminal"]
    assert terminals[-1].payload["finished_at"] is None
    with pytest.raises(FileExistsError):
        module.seed(output)
    assert len(list(JsonlSessionStore(tmp_path / "sessions").list())) == 1
