from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from agent.scheduled_tasks import (
    ScheduledOccurrenceOutcome,
    ScheduledTaskService,
    ScheduledTaskStore,
)
from agent.scheduling import OnceSchedule
from agent.sessions.events import Event
from agent.sessions.store import SessionStore
from agent_bridge.protocols.im.provider import OutboundSendResult
from agent_bridge.protocols.im.runtime import IMRuntime
from agent_bridge.scheduled_tasks.notifications import ScheduledIMNotifications, final_run_text

NOW = datetime(2026, 10, 27, tzinfo=UTC)
A = "qq|bot|c2c|user-a|session"
B = "qq|bot|c2c|user-b|session"


def event(kind: Any, run_id: str, **payload: Any) -> Event:
    return Event(type=kind, uuid=str(uuid4()), ts=NOW, payload=payload, meta={"run_id": run_id})


def setup(tmp_path: Path, *, enabled: bool = True, outcome: str = "completed") -> tuple[Any, ...]:
    store = ScheduledTaskStore(tmp_path / "tasks.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    task = service.create(
        name="daily",
        instruction="work",
        target_session_id="session",
        persona_name="default",
        model_id="fake/model",
        schedule=OnceSchedule(NOW.replace(tzinfo=None) + timedelta(hours=1), "UTC"),
        notify_bound_im=enabled,
    )
    occurrence = service.run_now(task.id)
    claim = store.claim_pending(NOW)[0].occurrence
    store.mark_terminal(
        occurrence.id, outcome=ScheduledOccurrenceOutcome(outcome), reason_code="test", now=NOW
    )
    sessions = Mock()
    sessions.query.return_value = SimpleNamespace(
        active_events=[
            event("assistant_message", claim.run_id or "", content="result", phase="final_answer"),
            event(
                "assistant_message",
                "other-run",
                content="PRIVATE OTHER RESULT",
                phase="final_answer",
            ),
        ]
    )
    im = Mock()
    im.presence_snapshots.return_value = [
        SimpleNamespace(session_id="session", destination_id=A),
        SimpleNamespace(session_id="session", destination_id=B),
        SimpleNamespace(session_id="other", destination_id="PRIVATE DESTINATION"),
    ]
    im.send_proactive.return_value = OutboundSendResult("delivered", "receipt")
    worker = ScheduledIMNotifications(store, cast(SessionStore, sessions), cast(IMRuntime, im))
    return store, service, task, claim, sessions, im, worker


def test_results_go_to_all_bound_targets_once_and_keep_run_identity(tmp_path: Path) -> None:
    store, _, _, occurrence, _, im, worker = setup(tmp_path)
    worker.deliver_pending()
    worker.deliver_pending()
    assert im.send_proactive.call_count == 2
    assert {call.args[0] for call in im.send_proactive.call_args_list} == {A, B}
    assert all(call.args[1].endswith("result") for call in im.send_proactive.call_args_list)
    summary = store.notifications.summary(occurrence.id)
    assert summary["state"] == "settled"
    assert [item["state"] for item in summary["deliveries"]] == ["delivered", "delivered"]


@pytest.mark.parametrize("outcome", ["failed", "interrupted", "rejected"])
def test_empty_failure_has_safe_status_without_model_or_business_rerun(
    tmp_path: Path, outcome: str
) -> None:
    store, _, _, occurrence, sessions, im, worker = setup(tmp_path, outcome=outcome)
    sessions.query.return_value.active_events = []
    worker.deliver_pending()
    assert im.send_proactive.call_count == 2
    assert "请在原会话查看执行记录" in im.send_proactive.call_args.args[1]
    assert store.get_occurrence(occurrence.id).outcome.value == outcome


@pytest.mark.parametrize("outcome", ["cancelled", "skipped", "expired"])
def test_non_notified_outcomes_are_recorded_without_send(tmp_path: Path, outcome: str) -> None:
    store, _, _, occurrence, _, im, worker = setup(tmp_path, outcome=outcome)
    worker.deliver_pending()
    im.send_proactive.assert_not_called()
    assert store.notifications.summary(occurrence.id)["reason"] == "outcome_not_notified"


def test_disabled_and_no_bindings_do_not_send(tmp_path: Path) -> None:
    store, _, _, occurrence, _, im, worker = setup(tmp_path, enabled=False)
    worker.deliver_pending()
    assert store.notifications.summary(occurrence.id)["state"] == "disabled"
    im.presence_snapshots.assert_not_called()
    other = tmp_path / "other"
    other.mkdir()
    store, _, _, occurrence, _, im, worker = setup(other)
    im.presence_snapshots.return_value = []
    worker.deliver_pending()
    assert store.notifications.summary(occurrence.id)["reason"] == "no_binding"
    im.send_proactive.assert_not_called()


def test_restart_resumes_pending_but_never_unknown_or_failed_targets(tmp_path: Path) -> None:
    store, _, _, occurrence, sessions, im, worker = setup(tmp_path)
    store.notifications.prepare(occurrence.id, "frozen result", (A, B))
    assert store.notifications.claim(occurrence.id, A)
    restarted = ScheduledTaskStore(store.path)
    worker = ScheduledIMNotifications(restarted, sessions, im)
    im.send_proactive.return_value = OutboundSendResult("failed", error_kind="platform_rejected")
    worker.deliver_pending()
    worker.deliver_pending()
    im.send_proactive.assert_called_once_with(B, "frozen result", text_format="markdown")
    deliveries = restarted.notifications.summary(occurrence.id)["deliveries"]
    assert [(item["state"], item["attempt_count"]) for item in deliveries] == [
        ("unknown", 1),
        ("failed", 1),
    ]


def test_concurrent_claims_send_once_per_destination(tmp_path: Path) -> None:
    store, _, _, occurrence, sessions, im, worker = setup(tmp_path)
    other = ScheduledIMNotifications(ScheduledTaskStore(store.path), sessions, im)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda current: current.deliver_pending(), [worker, other]))
    assert im.send_proactive.call_count == 2
    assert all(
        item["attempt_count"] == 1
        for item in store.notifications.summary(occurrence.id)["deliveries"]
    )


def test_send_timeout_and_changed_binding_have_independent_results(tmp_path: Path) -> None:
    store, _, _, occurrence, _, im, worker = setup(tmp_path)
    im.send_proactive.side_effect = [
        OutboundSendResult("unknown", error_kind="send_timeout"),
        OutboundSendResult("failed", error_kind="binding_changed"),
    ]
    worker.deliver_pending()
    worker.deliver_pending()
    assert [d["state"] for d in store.notifications.summary(occurrence.id)["deliveries"]] == [
        "unknown",
        "skipped",
    ]
    assert store.get_occurrence(occurrence.id).outcome is ScheduledOccurrenceOutcome.COMPLETED


def test_receipt_write_failure_does_not_repeat_external_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, occurrence, sessions, im, worker = setup(tmp_path)
    im.presence_snapshots.return_value = [SimpleNamespace(session_id="session", destination_id=A)]
    monkeypatch.setattr(
        store.notifications, "settle", Mock(side_effect=OSError("disk unavailable"))
    )
    worker.deliver_pending()
    assert im.send_proactive.call_count == 1
    restarted = ScheduledTaskStore(store.path)
    ScheduledIMNotifications(restarted, sessions, im).deliver_pending()
    assert im.send_proactive.call_count == 1
    assert restarted.notifications.summary(occurrence.id)["deliveries"][0]["state"] == "unknown"


def test_pre_submit_rejection_can_notify_without_session_body(tmp_path: Path) -> None:
    store, _, _, occurrence, sessions, im, worker = setup(tmp_path, outcome="rejected")
    sessions.query.side_effect = FileNotFoundError("no conversation yet")
    worker.deliver_pending()
    assert "请求未能开始执行" in im.send_proactive.call_args.args[1]
    assert store.notifications.summary(occurrence.id)["state"] == "settled"


def test_final_phase_and_tool_boundaries_exclude_private_and_partial_text() -> None:
    events = [
        event("assistant_message", "run", content="before tool", phase="unknown"),
        event("tool_call_result", "run", text="PRIVATE TOOL OUTPUT"),
        event("assistant_message", "run", content="working", phase="commentary"),
        event("assistant_message", "run", content="result", phase="final_answer"),
        event("assistant_message", "run", content="partial", phase="final_answer", partial=True),
        event("assistant_phase", "run", mode="native", validation="valid"),
    ]
    assert final_run_text(events, "run") == "result"
    events[-1].payload["validation"] = "conflict"
    assert final_run_text(events, "run") == ""
    assert final_run_text([event("assistant_message", "run", content="legacy")], "run") == "legacy"
    assert (
        final_run_text(
            [event("assistant_message", "run", content="working", phase="commentary")], "run"
        )
        == ""
    )


def test_slow_delivery_does_not_block_tracker_and_stop_prevents_next_target(tmp_path: Path) -> None:
    from agent_bridge.scheduled_tasks import ScheduledRunTracker

    store, _, _, _, _, im, worker = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def send(*args: Any, **kwargs: Any) -> OutboundSendResult:
        entered.set()
        assert release.wait(5)
        return OutboundSendResult("delivered")

    im.send_proactive.side_effect = send
    tracker = ScheduledRunTracker(store, notifications=worker)
    tracker.start()
    try:
        assert entered.wait(3)
        assert not worker.stop(timeout=0.01)
        release.set()
        assert tracker.stop(timeout=3)
        assert im.send_proactive.call_count == 1
    finally:
        release.set()
        tracker.stop(timeout=3)
