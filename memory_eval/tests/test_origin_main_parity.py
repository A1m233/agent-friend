from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

_PROBE = Path(__file__).with_name("_origin_main_parity_probe.py")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_e0r0_matches_real_origin_main_pipeline(tmp_path: Path) -> None:
    origin = os.environ.get("AGENT_FRIEND_ORIGIN_MAIN_WORKTREE")
    if not origin:
        pytest.skip("需要显式提供隔离的 origin/main worktree")
    origin_root = Path(origin).resolve()

    current = _run_probe(_REPO_ROOT, tmp_path / "current", "current.json")
    baseline = _run_probe(origin_root, tmp_path / "origin", "origin.json")

    assert current == baseline


def _run_probe(repo_root: Path, run_dir: Path, output_name: str) -> dict[str, object]:
    run_dir.mkdir(parents=True)
    output = run_dir / output_name
    python_paths = [
        repo_root / "memory_eval" / "src",
        repo_root / "memory" / "src",
        repo_root / "agent" / "src",
        repo_root / "llm_providers" / "src",
        repo_root / "runtime_config" / "src",
        _REPO_ROOT / ".venv" / "Lib" / "site-packages",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in python_paths)
    subprocess.run(
        [sys.executable, str(_PROBE), str(run_dir / "memory.db"), str(output)],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("parity probe 输出必须是 JSON object")
    return cast(dict[str, object], payload)
