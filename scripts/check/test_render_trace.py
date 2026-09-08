import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "render_trace", Path(__file__).parents[1] / "render-trace" / "run.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_summary_distinguishes_presented_from_displayed_and_omits_app_names(tmp_path):
    path = tmp_path / "trace.csv"
    path.write_text(
        "Application,ProcessID,SwapChainAddress,PresentMode,DisplayedTime\n"
        "private.exe,123,0xabc,Composed: Flip,16.6\n"
        "private.exe,123,0xabc,Composed: Flip,NA\n",
        encoding="utf-8",
    )
    result = module.summarize(path)
    assert result["streams"][0] == {
        "pid": 123,
        "swapchain": "0xabc",
        "presents": 2,
        "displayed": 1,
        "notDisplayed": 1,
    }
    assert result["windowMapping"] == "not-verified"
    assert "private" not in json.dumps(result)


def test_missing_display_metrics_are_unknown_not_zero_drops(tmp_path):
    path = tmp_path / "trace.csv"
    path.write_text("ProcessID,SwapChainAddress\n123,0xabc\n", encoding="utf-8")
    assert module.summarize(path)["missingDisplayMetrics"] == 1
    with pytest.raises(ValueError, match="oversized"):
        module.summarize(path, max_bytes=1)


def test_no_implicit_tool_install_or_elevation():
    result = module.doctor(None)
    assert result["ready"] is False
    assert result["automaticElevation"] is False
    assert result["automaticDownload"] is False


def test_capture_launch_failure_leaves_a_partial_manifest(tmp_path, monkeypatch):
    executable = tmp_path / "presentmon.exe"
    executable.write_bytes(b"test tool")
    monkeypatch.setattr(module, "doctor", lambda _: {"ready": True})
    monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)

    def fail(*args, **kwargs):
        raise OSError("private path must not be included")

    monkeypatch.setattr(module.subprocess, "Popen", fail)
    output = tmp_path / "capture"
    result = module.capture(executable, [123], 1, 1, output)
    assert result["outcome"] == "partial"
    assert result["stopReason"] == "capture-start-or-monitor-failed"
    assert json.loads((output / "manifest.json").read_text()) == result
    assert "private path" not in json.dumps(result)


def test_capture_size_limit_stops_only_owned_session_and_caps_retained_bytes(tmp_path, monkeypatch):
    executable = tmp_path / "presentmon.exe"
    executable.write_bytes(b"test tool")
    monkeypatch.setattr(module, "doctor", lambda _: {"ready": True})
    monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    calls = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

        def wait(self, **kwargs):
            return self.returncode

    def start(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--output_file") + 1]).write_bytes(b"x" * (1024 * 1024 + 64))
        return Process()

    def stop(command, **kwargs):
        calls.append(command)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    monkeypatch.setattr(module.subprocess, "run", stop)
    result = module.capture(executable, [123, 456], 1, 1, tmp_path / "capture")
    assert result["outcome"] == "partial"
    assert result["stopReason"] == "size-limit"
    assert result["traceBytes"] == 1024 * 1024
    assert result["retentionTruncated"] is True
    assert calls[0].count("--process_id") == 2
    assert "--no_track_input" in calls[0]
    assert calls[1][1:] == ["--session_name", result["session"], "--terminate_existing_session"]
