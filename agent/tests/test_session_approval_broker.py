from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import (
    ApprovalBroker,
    ApprovalModeConflictError,
    ApprovalRequestStaleError,
    CatastrophicActionPolicy,
)
from agent.command_runtime.models import (
    CommandApprovalDetails,
    CommandSkillBinding,
    PendingCommandApproval,
)
from agent.filesystem.models import (
    ApprovalAction,
    ApprovalItem,
    PendingFilesystemApproval,
)
from agent.sessions.metadata import SessionMetadataStore


def _request(*, target: Path | None = None) -> PendingFilesystemApproval:
    return PendingFilesystemApproval.create(
        session_id="sid-1",
        run_id="run-1",
        tool_call_id=f"tool-{time.monotonic_ns()}",
        reason="test",
        items=(ApprovalItem("item-1", Path("C:/work/file.txt"), "file", frozenset({"edit"})),),
        actions=(
            (ApprovalAction("action-1", "delete", target, recursive=True),)
            if target is not None
            else ()
        ),
    )


def _command_request(
    *,
    execution: str = "host_user",
    argv: tuple[str, ...] = ("echo", "ok"),
    skill: CommandSkillBinding | None = None,
) -> PendingCommandApproval:
    return PendingCommandApproval.create(
        session_id="sid-1",
        run_id="run-1",
        tool_call_id=f"tool-{time.monotonic_ns()}",
        reason="test command",
        details=CommandApprovalDetails(
            cmd=" ".join(argv),
            argv=argv,
            execution=execution,  # type: ignore[arg-type]
            workdir="C:/work",
            cwd_source="explicit",
            mode="buffered",
            timeout_seconds=30,
            filesystem_access="unrestricted" if execution == "host_user" else "read_only",
            additional_roots=(),
            host_derived_roots=(),
            network_access="unrestricted" if execution == "host_user" else "disabled",
            interactive=False,
            skill=skill,
        ),
    )


def _skill_replacement_request(
    argv: tuple[str, ...] = (
        "python",
        "scripts/install-skill-from-github.py",
        "--update",
        "demo",
        "--replace-local-changes",
    ),
) -> PendingCommandApproval:
    return _command_request(
        execution="sandboxed",
        argv=argv,
        skill=CommandSkillBinding(
            skill_id="builtin/skill-installer",
            entry="scripts/install-skill-from-github.py",
            workdir=".",
            fingerprint="a" * 64,
        ),
    )


def _broker(tmp_path: Path) -> ApprovalBroker:
    return ApprovalBroker(
        mode_store=SessionMetadataStore(tmp_path / "metadata.json"),
        risk_policy=CatastrophicActionPolicy(
            path_flavor="windows",
            home=Path("C:/Users/test"),
        ),
        session_exists=lambda session_id: session_id == "sid-1",
    )


def _wait_pending(broker: ApprovalBroker, count: int = 1) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if len(broker.pending(session_id="sid-1")) == count:
            return
        time.sleep(0.01)
    raise AssertionError("approval request did not become pending")


def test_session_auto_fully_approves_normal_request(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)
    request = _request()

    decision = broker.request(request, timeout_seconds=0.1)

    assert decision.approved is True
    assert decision.approved_permissions == {"item-1": frozenset({"edit"})}
    assert request.status == "approved"
    assert request.decision_source == "session_auto"
    assert request.requested_lifetime == "once"
    assert request.to_dict()["decision_source"] == "session_auto"
    assert request.to_dict()["approval_mode"] == "session_auto"
    assert broker.pending() == ()


def test_session_auto_keeps_explicit_host_user_command_once_scoped(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)
    request = _command_request(execution="host_user")

    decision = broker.request(request, timeout_seconds=0.1)

    assert decision.approved is True
    assert decision.lifetime == "once"
    assert request.details.execution == "host_user"
    assert request.decision_source == "session_auto"
    assert request.to_dict()["decision_source"] == "session_auto"


@pytest.mark.parametrize(
    "argv",
    [
        (
            "python",
            "scripts/install-skill-from-github.py",
            "--update",
            "demo",
            "--replace-local-changes",
        ),
        (
            "cmd.exe",
            "/c",
            'set "PYTHONPATH=scripts" && python scripts\\install-skill-from-github.py '
            "--update demo --replace-local-changes",
        ),
    ],
)
def test_session_auto_keeps_skill_replacement_pending_for_explicit_user(
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    broker = _broker(tmp_path)
    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)
    request = _skill_replacement_request(argv)
    result: list[Any] = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request(request, timeout_seconds=2)),
        daemon=True,
    )
    thread.start()

    _wait_pending(broker)
    assert request.approval_mode == "session_auto"
    assert request.status == "pending"
    assert request.risk_assessment is not None
    assert request.risk_assessment.level == "explicit"
    assert broker.decide(
        request.request_id,
        request.fully_approved_decision(),
        session_id="sid-1",
        run_id="run-1",
        expected_kind="command",
    )
    thread.join(timeout=2)

    assert result and result[0].approved is True
    assert request.decision_source == "user_once"


def test_session_auto_denies_catastrophic_request(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)
    request = _request(target=Path("C:/"))

    decision = broker.request(request, timeout_seconds=0.1)

    assert decision.approved is False
    assert request.status == "denied"
    assert request.decision_source == "catastrophic_policy"
    assert request.risk_assessment is not None
    assert request.risk_assessment.code == "critical-root-recursive-delete"
    assert request.to_dict()["risk"] == request.risk_assessment.to_dict()


def test_composer_mode_switch_resolves_existing_normal_pending(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    request = _request()
    result: list[Any] = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request(request, timeout_seconds=2)),
        daemon=True,
    )
    thread.start()
    _wait_pending(broker)

    state = broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)

    assert state.mode == "session_auto"
    assert broker.pending(session_id="sid-1") == ()
    thread.join(timeout=2)
    assert result and result[0].approved is True
    assert request.decision_source == "session_auto"


def test_composer_mode_switch_denies_existing_catastrophic_pending(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    request = _request(target=Path("C:/"))
    result: list[Any] = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request(request, timeout_seconds=2)),
        daemon=True,
    )
    thread.start()
    _wait_pending(broker)

    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)

    thread.join(timeout=2)
    assert result and result[0].approved is False
    assert request.decision_source == "catastrophic_policy"
    assert broker.pending(session_id="sid-1") == ()


def test_composer_mode_switch_preserves_existing_explicit_user_request(
    tmp_path: Path,
) -> None:
    broker = _broker(tmp_path)
    request = _skill_replacement_request()
    result: list[Any] = []
    thread = threading.Thread(
        target=lambda: result.append(broker.request(request, timeout_seconds=2)),
        daemon=True,
    )
    thread.start()
    _wait_pending(broker)

    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)

    assert request.status == "pending"
    assert request.approval_mode == "session_auto"
    assert broker.pending(session_id="sid-1") == (request,)
    assert broker.decide(
        request.request_id,
        request.fully_approved_decision(),
        session_id="sid-1",
        run_id="run-1",
        expected_kind="command",
    )
    thread.join(timeout=2)
    assert result and result[0].approved is True


def test_card_combination_resolves_current_and_remaining_requests(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    first = _request()
    second = _request()
    results: dict[str, Any] = {}
    threads = [
        threading.Thread(
            target=lambda req=request: results.__setitem__(
                req.request_id,
                broker.request(req, timeout_seconds=2),
            ),
            daemon=True,
        )
        for request in (first, second)
    ]
    for thread in threads:
        thread.start()
    _wait_pending(broker, 2)

    state = broker.decide_and_enable_session_auto(
        first.request_id,
        session_id="sid-1",
        run_id="run-1",
        expected_request_version=1,
        expected_mode_version=0,
    )

    assert state.mode == "session_auto"
    assert broker.pending(session_id="sid-1") == ()
    for thread in threads:
        thread.join(timeout=2)
    assert results[first.request_id].approved is True
    assert first.decision_source == "user_enable_session_auto"
    assert results[second.request_id].approved is True
    assert second.decision_source == "session_auto"


def test_stale_card_request_does_not_change_mode(tmp_path: Path) -> None:
    broker = _broker(tmp_path)

    with pytest.raises(ApprovalRequestStaleError):
        broker.decide_and_enable_session_auto(
            "missing",
            session_id="sid-1",
            run_id="run-1",
            expected_request_version=1,
            expected_mode_version=0,
        )

    assert broker.mode_state("sid-1").mode == "per_request"
    assert broker.mode_state("sid-1").version == 0


def test_stale_mode_version_returns_current_state(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.set_session_mode("sid-1", mode="session_auto", expected_version=0)

    with pytest.raises(ApprovalModeConflictError) as exc_info:
        broker.set_session_mode("sid-1", mode="per_request", expected_version=0)

    assert exc_info.value.current.mode == "session_auto"
    assert exc_info.value.current.version == 1
