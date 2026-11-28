from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from agent.command_runtime.recovery import RecoveryUnavailable, SandboxRecovery


def completed(recovery: SandboxRecovery, identity: str) -> dict[str, Any]:
    with recovery._condition:
        assert recovery._condition.wait_for(lambda: recovery._flight is None, timeout=3)
    result = recovery.snapshot(identity)
    assert result is not None
    return result


def test_http_retry_and_concurrent_keys_share_one_durable_operation() -> None:
    entered, release = threading.Event(), threading.Event()
    saved: list[dict[str, Any]] = []
    calls = []

    def persist(records: list[dict[str, Any]]) -> None:
        saved[:] = records

    def repair(check: Callable[[], None]) -> None:
        assert saved and saved[0]["request_keys"] == ["first"]
        calls.append("repair")
        entered.set()
        assert release.wait(2)
        check()

    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: True,
        repair=repair,
        retryable=lambda _: False,
        persist=persist,
        runtime_instance_id="runtime-a",
    )
    first = recovery.start("first")
    assert entered.wait(2)
    joined = recovery.start("second")
    assert joined["recovery_id"] == first["recovery_id"]
    assert recovery.start("first")["recovery_id"] == first["recovery_id"]
    release.set()
    final = completed(recovery, first["recovery_id"])
    assert final["result"] == "succeeded"
    assert final["runtime_instance_id"] == "runtime-a"
    assert "request_keys" not in final
    assert recovery.start("second") == final
    assert calls == ["repair"]


def test_blocked_retries_are_cached_until_explicit_recheck_or_fact_change() -> None:
    blockers = [{"operation_id": "proc_busy", "reason": "active_command", "version": 2}]
    checks, repairs = [], []
    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: not blockers,
        repair=lambda _: repairs.append(1),
        retryable=lambda _: False,
        reconcile=lambda: checks.append(1),
        blockers=lambda: list(blockers),
    )
    first = recovery.start("one")
    assert completed(recovery, first["recovery_id"])["result"] == "blocked"
    assert recovery.start("two")["recovery_id"] == first["recovery_id"]
    assert len(checks) == 1 and repairs == []
    recheck = recovery.start("three", recheck=True)
    assert recheck["recovery_id"] != first["recovery_id"]
    completed(recovery, recheck["recovery_id"])
    blockers.clear()
    retry = recovery.start("four")
    assert completed(recovery, retry["recovery_id"])["result"] == "succeeded"
    assert repairs == [1]


def test_cancel_is_versioned_and_does_not_claim_completion_before_cleanup() -> None:
    entered, release = threading.Event(), threading.Event()

    def repair(check: Callable[[], None]) -> None:
        entered.set()
        assert release.wait(2)
        check()

    recovery = SandboxRecovery(
        ready=lambda: True, idle=lambda: True, repair=repair, retryable=lambda _: False
    )
    initial = recovery.start("one")
    assert entered.wait(2)
    current = recovery.snapshot(initial["recovery_id"])
    assert current is not None
    with pytest.raises(RecoveryUnavailable, match="version changed"):
        recovery.cancel(current["recovery_id"], current["version"] - 1)
    stopping = recovery.cancel(current["recovery_id"], current["version"])
    assert stopping["result"] is None and stopping["phase"] == "stopping"
    assert recovery.cancel(current["recovery_id"], current["version"]) == stopping
    release.set()
    assert completed(recovery, current["recovery_id"])["result"] == "cancelled"


def test_restart_preserves_request_identity_and_marks_interrupted() -> None:
    saved = [
        {
            "recovery_id": "old",
            "runtime_instance_id": "old-runtime",
            "version": 3,
            "result": None,
            "phase": "repairing",
            "request_keys": ["lost-response"],
        }
    ]
    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: True,
        repair=lambda _: pytest.fail("must not replay"),
        retryable=lambda _: False,
        history=saved,
    )
    restored = recovery.start("lost-response")
    assert restored["recovery_id"] == "old" and restored["result"] == "interrupted"
    assert restored["version"] == 4


def test_failed_persistence_never_launches_repair() -> None:
    def persist(_: object) -> None:
        raise OSError("disk full")

    recovery = SandboxRecovery(
        ready=lambda: True,
        idle=lambda: True,
        repair=lambda _: pytest.fail("must not launch"),
        retryable=lambda _: False,
        persist=persist,
    )
    with pytest.raises(OSError, match="disk full"):
        recovery.start("one")
    assert recovery._flight is None
