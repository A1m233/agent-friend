from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent.latency import RunLatencyContext, current_run_phase, monotonic_ms, observe_run_phase


def test_phases_close_on_cancellation_without_logging_private_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Cancelled(Exception):
        cancellation = SimpleNamespace(reason="explicit_request")

    logger = logging.getLogger("test.latency")
    caplog.set_level(logging.INFO)
    context = RunLatencyContext("run", "session", monotonic_ms())
    with (
        pytest.raises(Cancelled),
        observe_run_phase(logger, context, "outer"),
        observe_run_phase(logger, context, "inner"),
    ):
        raise Cancelled("private body and credential")
    done = [
        getattr(r, "run_latency", {})
        for r in caplog.records
        if getattr(r, "run_latency", {}).get("event") == "prepare_phase_done"
    ]
    assert [(r["phase"], r["status"]) for r in done] == [
        ("inner", "cancelled"),
        ("outer", "cancelled"),
    ]
    assert done[0]["parent_phase_id"] == done[1]["phase_id"]
    assert done[1]["parent_phase_id"] is None
    assert "private body" not in caplog.text
    assert all(r["duration_ms"] >= 0 and r["thread_cpu_ms"] >= 0 for r in done)
    assert current_run_phase() is None


def test_telemetry_failure_does_not_replace_business_result_or_exception() -> None:
    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise ValueError("telemetry failed")

    logger = logging.Logger("broken", level=logging.INFO)
    logger.addHandler(BrokenHandler())
    ctx = RunLatencyContext("run", "session", monotonic_ms())
    with observe_run_phase(logger, ctx, "success"):
        assert current_run_phase() == ("run", 1)
    error = RuntimeError("business")
    with pytest.raises(RuntimeError) as captured, observe_run_phase(logger, ctx, "failure"):
        raise error
    assert captured.value is error
    assert current_run_phase() is None


def test_phase_context_does_not_cross_workers() -> None:
    def run(name: str) -> tuple[str, int] | None:
        assert current_run_phase() is None
        with observe_run_phase(
            logging.getLogger(__name__), RunLatencyContext(name, name, monotonic_ms()), "prepare"
        ):
            return current_run_phase()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["a", "b"])) == [("a", 1), ("b", 1)]
    assert current_run_phase() is None


def test_offline_probe_sends_and_preserves_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent import BUILTIN_DEFAULT_PERSONA_ID, JsonlSessionStore, SessionManager

    monkeypatch.setenv("AGENT_FRIEND_DATA_DIR", str(tmp_path / "isolated"))
    monkeypatch.setenv("AGENT_FRIEND_LOG_DIR", str(tmp_path / "isolated-logs"))
    script = Path(__file__).resolve().parents[2] / "scripts/session-latency/offline_probe.py"
    spec = importlib.util.spec_from_file_location("offline_probe", script)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    original = tmp_path / "source"
    store = JsonlSessionStore(original)
    manager = SessionManager(store)
    sid = manager.create(
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model="deepseek/deepseek-chat",
        title="fixture",
    ).session_id
    source = original / f"{sid}.jsonl"
    before = source.read_bytes()
    destination = tmp_path / "isolated/sessions"
    destination.mkdir(parents=True)
    metadata = probe.copy_fixture(source, destination)
    assert metadata["session_id"] == sid
    result = probe.measure(destination.parent, {"A": sid}, ["A", "A"])
    assert [row["outcome"] for row in result["runs"]] == ["completed", "completed"], result["runs"]
    assert [row["provider_calls"] for row in result["runs"]] == [1, 1]
    assert source.read_bytes() == before
    assert (destination / source.name).stat().st_size > len(before)
    phases = {
        row.get("phase")
        for row in result["observations"]
        if row.get("event") == "prepare_phase_done"
    }
    assert {
        "input_persistence",
        "accepted_rollout",
        "recall_boundary",
        "memory_retrieve",
        "provider_preparation",
    } <= phases
    assert "请回顾" not in json.dumps(result, ensure_ascii=False)
    with pytest.raises(FileExistsError):
        probe.copy_fixture(source, destination)
    with pytest.raises(RuntimeError, match="network_forbidden"):
        probe.deny_network()
    cleaned = []

    async def sdk_cleanup() -> None:
        cleaned.append(True)

    loop = asyncio.new_event_loop()
    cleanup_task = loop.create_task(sdk_cleanup())
    probe.close_offline_loop(loop)
    assert cleaned == [True]
    assert cleanup_task.done()
    assert loop.is_closed()
