from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "scripts/command-runtime"))
    return importlib.import_module("lifecycle_probe.probe")


def test_probe_environment_does_not_inherit_credentials_or_product_state(
    probe: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy")
    monkeypatch.setenv("CODEX_HOME", "existing-product-state")
    monkeypatch.setenv("PYTHONPATH", "untrusted-python-path")
    monkeypatch.setenv("PYTHONHOME", "untrusted-python-home")
    env = probe.isolated_environment(tmp_path)
    for key in ("OPENAI_API_KEY", "HTTPS_PROXY", "PYTHONPATH", "PYTHONHOME"):
        assert key not in env
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "TEMP", "TMP"):
        assert Path(env[key]).is_relative_to(tmp_path)
        assert Path(env[key]).is_dir()


def test_probe_preserves_existing_report_before_starting_any_process(
    probe: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_text("previous evidence", encoding="utf-8")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("report collision must be rejected before any process launch")

    monkeypatch.setattr(probe.subprocess, "Popen", unexpected)
    with pytest.raises(ValueError, match="report_exists"):
        probe.run(tmp_path / "missing.exe", report)
    assert report.read_text(encoding="utf-8") == "previous evidence"


@pytest.mark.skipif(os.name != "nt", reason="Windows native Job qualification")
def test_failed_native_worker_is_cleaned_without_existing_sandbox(
    probe: ModuleType, tmp_path: Path
) -> None:
    result = probe.run_case("server-loss", tmp_path / "missing.exe", tmp_path)
    assert result["status"] == "failed"
    assert "exec_server_start_failed" in result["failure"]
    assert result["remaining_job_processes"] == 0
