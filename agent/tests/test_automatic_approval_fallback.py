from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.approvals.automatic import AutomaticApprovals
from agent.approvals.reviewer import ReviewAssessment
from agent.sessions.metadata import SessionMetadataStore

from .test_automatic_approvals import allow, request


def _broker(tmp_path: Path, outcome: str) -> ApprovalBroker:
    def review(*_args: object) -> ReviewAssessment:
        if outcome == "failed":
            raise ValueError("malformed review")
        if outcome == "timed_out":
            raise TimeoutError("review timeout")
        return ReviewAssessment("deny", "high", "unknown", "Needs explicit confirmation")

    broker = ApprovalBroker(mode_store=SessionMetadataStore(tmp_path / "meta.json"))
    broker.set_session_mode("session", mode="auto_review", expected_version=0)
    broker.automatic = AutomaticApprovals(policy=lambda: ("auto_review", 1), review=review)
    return broker


@pytest.mark.parametrize("outcome", ["failed", "timed_out", "denied"])
@pytest.mark.parametrize("approved", [True, False])
def test_review_falls_back_to_same_manual_request_without_changing_session_mode(
    tmp_path: Path, outcome: str, approved: bool
) -> None:
    broker = _broker(tmp_path, outcome)
    item = request(tmp_path / "note")
    ready = threading.Event()
    changes: list[dict[str, object]] = []
    results: list[Any] = []

    def changed(snapshot: dict[str, object]) -> None:
        changes.append(snapshot)
        if snapshot["status"] == "pending":
            ready.set()

    broker.add_listener(changed)
    worker = threading.Thread(
        target=lambda: results.append(
            broker.request(item, timeout_seconds=2, surface="desktop_chat")
        )
    )
    worker.start()
    try:
        assert ready.wait(1)
        assert worker.is_alive() and not results
        assert [c["status"] for c in changes] == ["reviewing", "pending"]
        assert item.approval_mode == "per_request"
        assert item.review_fallback == outcome
        assert broker.mode_state("session").mode == "auto_review"
        assert broker.pending_for_session("session") == (item,)
        assert not broker.decide(
            item.request_id, item.fully_approved_decision(), expected_version=2
        )
        decision = item.fully_approved_decision() if approved else item.cancelled_decision()
        assert broker.decide(item.request_id, decision, expected_version=item.version)
        worker.join(1)
        assert not worker.is_alive()
        assert results[0].approved is approved
        assert item.decision_source == "user_once"
        assert not broker.decide(item.request_id, decision)
        assert broker.pending_for_session("session") == ()
        assert broker.mode_state("session").mode == "auto_review"
    finally:
        broker.cancel_run("session", "run")
        worker.join(3)


@pytest.mark.parametrize(
    "boundary", ["inner", "no_surface", "critical", "cancel", "policy_changed"]
)
def test_terminal_boundaries_never_open_manual_approval(tmp_path: Path, boundary: str) -> None:
    broker = _broker(tmp_path, "failed")
    assert broker.automatic is not None
    if boundary == "inner":
        broker.is_companionship = lambda _: True
    elif boundary == "critical":
        broker.automatic.review = lambda *_: ReviewAssessment("deny", "critical", "high", "Unsafe")
    elif boundary in {"cancel", "policy_changed"}:

        def review(*_args: object) -> ReviewAssessment:
            if boundary == "cancel":
                broker.cancel_run("session", "run")
            else:
                broker.set_session_mode("session", mode="per_request", expected_version=1)
            raise ValueError("review failed after interruption")

        broker.automatic.review = review
    changes: list[dict[str, object]] = []
    broker.add_listener(changes.append)
    item = request(tmp_path / "note")
    decision = broker.request(
        item, timeout_seconds=0.1, surface="im:qq" if boundary == "no_surface" else "desktop_chat"
    )
    assert not decision.approved
    assert "pending" not in [c["status"] for c in changes]
    assert item.review_fallback is None


def test_manual_timeout_does_not_accept_late_review_result(tmp_path: Path) -> None:
    broker = _broker(tmp_path, "timed_out")
    release = threading.Event()
    finished = threading.Event()
    assert broker.automatic is not None

    def slow(*_args: object) -> ReviewAssessment:
        release.wait(1)
        finished.set()
        return allow()

    broker.automatic.review = slow
    item = request(tmp_path / "note")
    assert broker.request(item, timeout_seconds=0.02, surface="desktop_chat") is None
    assert item.status == "expired"
    assert item.review_fallback == "timed_out"
    release.set()
    assert finished.wait(1)
    assert item.status == "expired"
    assert not broker.decide(item.request_id, item.fully_approved_decision())
