from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.approvals.automatic import AutomaticApprovals
from agent.approvals.policy import authorization_policy
from agent.approvals.reviewer import (
    ReviewAssessment,
    SecurityReviewer,
    inspect_action_path,
    parse_assessment,
)
from agent.filesystem.models import ApprovalItem, PendingFilesystemApproval
from llm_providers.stream_events import LLMStreamEvent

from llm_providers import ModelRequest, ModelResponse


def test_global_policy_migrates_legacy_or_invalid_values_without_expanding_access() -> None:
    assert authorization_policy(None) == ("auto_review", 0)
    assert authorization_policy({"authorizationMode": "session_auto"}) == ("auto_review", 0)
    assert authorization_policy({"authorizationMode": [], "authorizationPolicyVersion": True}) == (
        "auto_review",
        0,
    )
    assert authorization_policy(
        {"authorizationMode": "full_access", "authorizationPolicyVersion": 2}
    ) == ("full_access", 2)


def request(path: Path, *, run: str = "run", reason: str = "read") -> PendingFilesystemApproval:
    return PendingFilesystemApproval.create(
        session_id="session",
        run_id=run,
        tool_call_id="call",
        reason=reason,
        items=(ApprovalItem("item", path, "file", frozenset({"read_text"})),),
        actions=(),
    )


def allow(*_args: object) -> ReviewAssessment:
    return parse_assessment('{"outcome":"allow"}')


def deny(*_args: object) -> ReviewAssessment:
    return parse_assessment(
        '{"outcome":"deny","rationale":"Sensitive egress without payload authorization."}'
    )


def test_chat_review_is_opt_in_and_independent_of_companionship(tmp_path: Path) -> None:
    from agent.sessions.metadata import SessionMetadataStore

    store = SessionMetadataStore(tmp_path / "metadata.json")
    broker = ApprovalBroker(mode_store=store)
    broker.is_companionship = lambda sid: sid == "inner"
    broker.automatic = AutomaticApprovals(policy=lambda: ("full_access", 9), review=deny)
    assert broker.mode_state("inner").mode == "full_access"
    assert broker.mode_state("session").mode == "per_request"
    broker.set_session_mode("session", mode="auto_review", expected_version=0)
    assert not broker.request(request(tmp_path / "file"), timeout_seconds=1).approved
    assert store.get("session").approval_mode == "auto_review"
    assert broker.mode_state("other").mode == "per_request"
    assert broker.mode_state("inner").mode == "full_access"
    with broker.run_mode("session", "run", "session_auto"):
        assert broker.request(request(tmp_path / "file"), timeout_seconds=1).approved


def test_automatic_works_without_human_surface_and_ignores_legacy_override(tmp_path: Path) -> None:
    broker = ApprovalBroker()
    broker.is_companionship = lambda _: True
    broker.automatic = AutomaticApprovals(policy=lambda: ("auto_review", 2), review=allow)
    changes: list[dict[str, object]] = []
    broker.add_listener(changes.append)
    with broker.run_mode("session", "run", "session_auto"):
        item = request(tmp_path / "note")
        assert broker.request(item, timeout_seconds=1, surface="inner_world").approved
    assert item.approval_mode == "auto_review"
    assert item.decision_source == "auto_review"
    assert [change["status"] for change in changes] == ["reviewing", "approved"]
    with pytest.raises(ValueError, match="自主陪伴"):
        broker.set_session_mode("session", mode="session_auto", expected_version=0)


@pytest.mark.parametrize(
    "answer",
    [
        "allow",
        '{"outcome":"maybe"}',
        '{"outcome":"allow","permission":"all"}',
        '{"outcome":"allow","risk_level":"wrong"}',
    ],
)
def test_invalid_assessments_never_allow(answer: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        parse_assessment(answer)


def test_assessment_defaults_and_hard_thresholds() -> None:
    assert parse_assessment('```json\n{"outcome":"allow"}\n```').risk_level == "low"
    assert (
        parse_assessment(
            '{"outcome":"allow","risk_level":"critical","user_authorization":"high"}'
        ).outcome
        == "deny"
    )
    assert parse_assessment('{"outcome":"allow","risk_level":"high"}').outcome == "deny"


def test_review_failure_timeout_and_cancellation_fail_closed(tmp_path: Path) -> None:
    broker = ApprovalBroker()
    broker.is_companionship = lambda _: True

    def broken(*_args: object) -> ReviewAssessment:
        raise ValueError("malformed output")

    broker.automatic = AutomaticApprovals(policy=lambda: ("auto_review", 0), review=broken)
    item = request(tmp_path / "note")
    assert not broker.request(item, timeout_seconds=1).approved
    assert item.status == "failed"
    release = threading.Event()

    def slow(*_args: object) -> ReviewAssessment:
        release.wait(1)
        return allow()

    broker.automatic.review = slow
    item = request(tmp_path / "note")
    assert not broker.request(item, timeout_seconds=0.01).approved
    assert item.status == "timed_out"
    release.set()
    time.sleep(0.02)
    assert item.status == "timed_out"
    release.clear()
    item = request(tmp_path / "note")
    worker = threading.Thread(target=lambda: broker.request(item, timeout_seconds=1))
    worker.start()
    for _ in range(100):
        if item.status == "reviewing":
            break
        time.sleep(0.001)
    broker.cancel_run("session", "run")
    worker.join(1)
    release.set()
    assert item.status == "aborted"
    assert item._decision is not None and not item._decision.approved


def test_duplicate_denial_and_exact_one_retry(tmp_path: Path) -> None:
    calls = []

    def review(_request: object, action: dict[str, Any], *_args: object) -> ReviewAssessment:
        calls.append(action)
        return deny()

    broker = ApprovalBroker()
    broker.is_companionship = lambda _: True
    stopped = []
    broker.automatic = AutomaticApprovals(
        policy=lambda: ("auto_review", 1), review=review, stop_run=lambda *key: stopped.append(key)
    )
    first = request(tmp_path / "note")
    broker.request(first, timeout_seconds=1)
    broker.request(request(tmp_path / "note", reason="different claim"), timeout_seconds=1)
    assert len(calls) == 1
    broker.automatic.reauthorize(first.request_id, "session", expected_version=first.version)
    broker.request(request(tmp_path / "note"), timeout_seconds=1)
    assert len(calls) == 2
    assert "host_user_reauthorization" in calls[-1]
    assert stopped == [("session", "run")]
    broker.request(request(tmp_path / "other"), timeout_seconds=1)
    assert len(calls) == 2


def test_policy_change_during_review_aborts(tmp_path: Path) -> None:
    version = [1]

    def review(*_args: object) -> ReviewAssessment:
        version[0] = 2
        return allow()

    broker = ApprovalBroker()
    broker.is_companionship = lambda _: True
    broker.automatic = AutomaticApprovals(policy=lambda: ("auto_review", version[0]), review=review)
    item = request(tmp_path / "note")
    assert not broker.request(item, timeout_seconds=1).approved
    assert item.status == "aborted"


def test_reviewer_uses_only_security_context_and_bounded_inspection(tmp_path: Path) -> None:
    seen = []

    class Model:
        context_window = 128000

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            return iter(())

        def generate(self, req: ModelRequest) -> ModelResponse:
            seen.append(req)
            return ModelResponse(
                '{"inspect":{"path":"' + tmp_path.as_posix() + '","offset":0}}'
                if len(seen) == 1
                else '{"outcome":"allow"}'
            )

    result = SecurityReviewer(Model()).review(
        {"kind": "filesystem"},
        [{"event_type": "user_message", "content": "create a note"}],
        inspect=lambda path, offset: inspect_action_path(path, offset, allowed_paths=(tmp_path,)),
        cancel_check=lambda: None,
        deadline=time.monotonic() + 5,
        session_id="s",
        run_id="r",
    )
    assert result.outcome == "allow"
    assert len(seen) == 2
    assert all(req.tools is None and not req.control.allow_retries for req in seen)
    assert "friend persona" not in seen[0].messages[0]["content"]
    assert "{{" not in seen[0].messages[0]["content"]
    assert "universal sandbox" in seen[0].messages[0]["content"]
    assert (
        inspect_action_path(str(tmp_path.parent / "private"), 0, allowed_paths=(tmp_path,))["error"]
        == "inspection_path_not_allowed"
    )
