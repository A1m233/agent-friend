from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.approvals import ApprovalBroker
from agent.approvals.automatic import AutomaticApprovals
from agent.command_runtime.models import CommandApprovalDecision
from agent.memory_maintenance.service import MaintenanceApplication, MaintenanceInvocation
from agent.memory_maintenance.sources import MaintenanceSources
from agent.sessions.events import Event
from agent.tool_runtime.control import ToolInvocationCancelled, ToolInvocationControl
from memory.maintenance.models import MaintenanceError
from memory.maintenance.service import SemanticMaintenance

from agent import JsonlSessionStore, Session
from memory import SemanticRow, SqliteMemoryStore


def memory() -> SemanticMaintenance:
    store = SqliteMemoryStore(":memory:")
    now = datetime.now(UTC)
    store.add_semantic(
        SemanticRow(
            id="fact",
            statement="old",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            provenance=["source#first..last", "episode-id", "source#missing"],
        )
    )
    return SemanticMaintenance(store, owner_user_id="local")


def batch(service: SemanticMaintenance) -> str:
    repo = service.repository
    aid = repo.start("local", "current")["audit_id"]
    repo.record(
        "local",
        aid,
        [
            {
                "semantic_id": "fact",
                "action": "retire",
                "reason": "one-off report",
                "source_status": "unverified",
                "evidence_refs": [],
            }
        ],
    )
    return str(repo.prepare("local", "current", aid, ["fact"])["batch_id"])


def invocation() -> MaintenanceInvocation:
    return MaintenanceInvocation("current", "run", "call", "desktop_chat", "current#user")


@pytest.mark.parametrize("allow", [True, False])
def test_real_broker_decides_entire_batch_once(allow: bool) -> None:
    service = memory()
    bid = batch(service)
    broker = ApprovalBroker()
    observed: list[dict[str, object]] = []

    def decide(value: dict[str, object]) -> None:
        if value["status"] != "pending":
            return
        observed.append(value)
        assert broker.decide(
            str(value["request_id"]),
            CommandApprovalDecision(allow=allow),
            session_id="current",
            run_id="run",
            expected_kind="memory_maintenance",
            lifetime="once",
        )

    broker.add_listener(decide)
    app = MaintenanceApplication(service, broker)
    result = app.apply(bid, invocation(), ToolInvocationControl())
    assert result["status"] == ("committed" if allow else "not_committed")
    assert len(observed) == 1
    details = observed[0]["details"]
    assert isinstance(details, dict) and details["batch_id"] == bid
    assert details["operations"][0]["targets"][0]["statement"] == "old"
    if allow:
        assert app.apply(bid, invocation(), ToolInvocationControl())["status"] == "committed"
        assert len(observed) == 1
    else:
        assert service.repository.detail("local", "fact")["state"] == "active"


def test_cancelled_approval_never_writes() -> None:
    service = memory()
    bid = batch(service)
    broker = ApprovalBroker()
    control = ToolInvocationControl()
    broker.add_listener(
        lambda value: control.cancel("user stopped") if value["status"] == "pending" else None
    )
    app = MaintenanceApplication(service, broker)
    result = app.apply(bid, invocation(), control)
    assert result["status"] == "not_committed"
    assert service.repository.detail("local", "fact")["state"] == "active"


def test_full_access_still_requires_user_origin_and_frozen_session() -> None:
    service = memory()
    bid = batch(service)
    broker = ApprovalBroker()

    def never_review(*args: Any) -> Any:
        raise AssertionError("full access must not call a model")

    broker.automatic = AutomaticApprovals(policy=lambda: ("full_access", 1), review=never_review)
    app = MaintenanceApplication(service, broker)
    for ctx in (
        replace(invocation(), user_source_ref=None),
        replace(invocation(), session_id="other"),
    ):
        with pytest.raises(MaintenanceError):
            app.apply(bid, ctx, ToolInvocationControl())
    with broker.run_mode("current", "run", "full_access"):
        result = app.apply(bid, invocation(), ToolInvocationControl())
    assert result["authorization"]["decision_source"] == "full_access"


def test_target_changed_during_approval_is_not_partially_applied() -> None:
    service = memory()
    bid = batch(service)
    broker = ApprovalBroker()

    def decide(value: dict[str, object]) -> None:
        if value["status"] == "pending":
            service.repository.conn.execute(
                "UPDATE semantic SET statement='changed' WHERE id='fact'"
            )
            service.repository.conn.commit()
            broker.decide(
                str(value["request_id"]),
                CommandApprovalDecision(allow=True),
                session_id="current",
                run_id="run",
                expected_kind="memory_maintenance",
                lifetime="once",
            )

    broker.add_listener(decide)
    with pytest.raises(MaintenanceError, match="stale_batch_target"):
        MaintenanceApplication(service, broker).apply(bid, invocation(), ToolInvocationControl())
    assert service.repository.detail("local", "fact")["state"] == "active"


@pytest.mark.parametrize("allow", [True, False])
def test_auto_review_receives_the_exact_memory_batch(allow: bool) -> None:
    from agent.approvals.reviewer import ReviewAssessment

    service = memory()
    bid = batch(service)
    broker = ApprovalBroker()
    reviewed = []

    def review(
        request: Any, action: dict[str, Any], check: Any, deadline: float
    ) -> ReviewAssessment:
        check()
        reviewed.append(action)
        return ReviewAssessment("allow" if allow else "deny", "low", "high", "fixture decision")

    broker.automatic = AutomaticApprovals(policy=lambda: ("auto_review", 1), review=review)
    # A rejected review's optional desktop fallback is explicitly declined.
    broker.add_listener(
        lambda value: (
            broker.cancel_run("current", "run")
            if value.get("review_fallback") and value["status"] == "pending"
            else None
        )
    )
    with broker.run_mode("current", "run", "auto_review"):
        result = MaintenanceApplication(service, broker).apply(
            bid, invocation(), ToolInvocationControl()
        )
    assert len(reviewed) == 1
    assert reviewed[0]["details"]["batch_id"] == bid
    assert reviewed[0]["details"]["digest"] == service.repository.batch("local", bid)["digest"]
    assert result["status"] == ("committed" if allow else "not_committed")


def sources(tmp_path: Path) -> tuple[MaintenanceSources, JsonlSessionStore]:
    store = JsonlSessionStore(tmp_path)
    session = Session.new(
        title="source", persona="default", model="deepseek/deepseek-chat", session_id="source"
    )
    store.create(session)
    now = datetime.now(UTC)
    store.append_event(
        "source",
        Event(
            type="user_message",
            uuid="first",
            ts=now,
            payload={
                "content": "scheduled input",
                "request_source": {"kind": "scheduled_task", "task_id": "task"},
            },
        ),
    )
    store.append_event(
        "source",
        Event(
            type="assistant_message", uuid="last", ts=now, payload={"content": "assistant report"}
        ),
    )
    return MaintenanceSources(memory(), store), store


def test_sources_keep_speaker_and_trigger_with_content_pagination(tmp_path: Path) -> None:
    reader, _ = sources(tmp_path)
    page = reader.read("fact", "source#first..last", limit=10)
    assert page["status"] == "available"
    assert page["messages"][0]["speaker"] == "user"
    assert page["messages"][0]["request_source"]["kind"] == "scheduled_task"
    next_page = reader.read("fact", "source#first..last", offset=page["next_content_offset"])
    assert next_page["messages"][-1]["speaker"] == "assistant"
    text = "".join(m["content"] for m in page["messages"] + next_page["messages"])
    assert text == "scheduled inputassistant report"


def test_missing_rewritten_and_episode_sources_are_explicit(tmp_path: Path) -> None:
    reader, store = sources(tmp_path)
    assert reader.read("fact", "episode-id")["reason"] == "not_a_direct_conversation_reference"
    assert reader.read("fact", "source#missing")["reason"] == "source_incomplete"
    with pytest.raises(MaintenanceError, match="source_not_linked_to_fact"):
        reader.read("fact", "other#event")
    store.append_event(
        "source",
        Event(
            type="turn_rewrite",
            uuid="rewrite",
            ts=datetime.now(UTC),
            payload={"inactive_event_uuids": ["first"]},
        ),
    )
    assert reader.read("fact", "source#first..last")["reason"] == "source_rewritten"


def test_source_cancellation_is_not_missing_evidence(tmp_path: Path) -> None:
    reader, _ = sources(tmp_path)
    control = ToolInvocationControl()
    control.cancel("stop")
    with pytest.raises(ToolInvocationCancelled):
        reader.read("fact", "source#first..last", control=control)
