"""Durability, isolation and transactional regressions for ordinary maintenance."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from memory.maintenance.models import MaintenanceAuthorization, MaintenanceError
from memory.maintenance.service import SemanticMaintenance
from memory.search.contracts import AnalyzedQuery, SearchRequest
from memory.search.structured_source import StructuredMemorySource
from memory.search.vector import VectorHit
from memory.store.maintenance import MaintenanceRepository

from memory import SemanticRow, SqliteMemoryStore


def add(store: SqliteMemoryStore, sid: str, **kw: Any) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.add_semantic(
        SemanticRow(
            id=sid,
            statement=f"original {sid}",
            persona_id="p1",
            created_at=now,
            updated_at=now,
            valid_from=now,
            provenance=[f"s1#{sid}"],
            **kw,
        )
    )


def decision(sid: str, action: str = "retire", **kw: Any) -> dict[str, Any]:
    return {
        "semantic_id": sid,
        "action": action,
        "reason": "checked original source",
        "source_status": "verified",
        "evidence_refs": [f"s1#{sid}"],
        **kw,
    }


def prepare(repo: MaintenanceRepository, *decisions: dict[str, Any]) -> dict[str, Any]:
    audit = repo.start("local", "current")
    repo.record("local", audit["audit_id"], list(decisions))
    return repo.prepare(
        "local", "current", audit["audit_id"], [d["semantic_id"] for d in decisions]
    )


def authorize(batch: dict[str, Any]) -> MaintenanceAuthorization:
    return MaintenanceAuthorization(
        batch["batch_id"], batch["digest"], "request1", "current", "user_once", "current#approved"
    )


def test_audit_is_fixed_resumable_and_does_not_write_facts(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SqliteMemoryStore(path)
    for sid in "abcd":
        add(store, sid)
    add(store, "pinned", pinned=True)
    add(store, "other-owner", owner_user_id="other")
    repo = store.maintenance_repository()
    audit = repo.start("local", "current")
    aid = audit["audit_id"]
    assert audit["total"] == 4
    page = repo.audit_page("local", aid, limit=2)
    repo.record("local", aid, [decision("a", "keep"), decision("b", "pending")])
    add(store, "new-after-snapshot")
    store.close()
    store = SqliteMemoryStore(path)
    repo = store.maintenance_repository()
    page = repo.audit_page("local", aid, offset=page["next_offset"], limit=2)
    assert [item["semantic_id"] for item in page["items"]] == ["c", "d"]
    assert page["next_offset"] is None
    status = repo.status("local", aid)
    assert (status["total"], status["read"], status["reviewed"], status["remaining"]) == (
        4,
        4,
        2,
        2,
    )
    assert not status["coverage_complete"]
    assert repo.audits("local")[0] == status
    assert len(store.list_active_ordinary_semantic(owner_user_id="local")) == 5
    assert store.maintenance_epoch("local") == 0
    store.close()


def test_ordinary_queries_never_read_other_layers() -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    add(store, "pinned", pinned=True)
    add(store, "other", owner_user_id="other")

    def guard(action: int, table: str | None, *unused: Any) -> int:
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and table in {"episodic", "core_profile_entry"}
            else sqlite3.SQLITE_OK
        )

    store._conn.set_authorizer(guard)
    repo = store.maintenance_repository()
    assert [item["semantic_id"] for item in repo.query("local")["items"]] == ["a"]
    assert repo.detail("local", "a")["source_refs"] == ["s1#a"]
    for sid in ("pinned", "other"):
        with pytest.raises(MaintenanceError, match="ordinary_fact_not_found"):
            repo.detail("local", sid)
    batch = prepare(repo, decision("a", "revoke"))
    repo.commit("local", authorize(batch))


@pytest.mark.parametrize(
    "action,disposition",
    [
        ("retire", "retire"),
        ("revoke", "revoke"),
        ("rewrite", "retire"),
        ("rewrite", "revoke"),
        ("merge", "retire"),
    ],
)
def test_changes_preserve_provenance_and_are_idempotent(action: str, disposition: str) -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    add(store, "b")
    repo = store.maintenance_repository()
    proposal = decision(
        "a", action, proposed_statement="new canonical fact", old_disposition=disposition
    )
    if action == "merge":
        proposal["merge_into"] = "b"
    batch = prepare(repo, proposal)
    receipt = repo.commit("local", authorize(batch))
    change = receipt["changes"][0]
    assert change["old_disposition"] == disposition
    old = repo.detail("local", "a")
    assert old["state"] == ("revoked" if disposition == "revoke" else "retired")
    assert all(item["semantic_id"] != "a" for item in repo.query("local")["items"])
    if action in {"rewrite", "merge"}:
        new = repo.detail("local", change["new_id"])
        assert new["statement"] == "new canonical fact"
        assert {"s1#a", "current#approved"} <= set(new["source_refs"])
        if action == "merge":
            assert "s1#b" in new["source_refs"]
            assert repo.detail("local", "b")["state"] == "retired"
        hits = store.search_semantic("canonical", owner_user_id="local", persona_id="p1", limit=10)
        assert [row.id for row, _ in hits] == [change["new_id"]]
    assert repo.commit("local", authorize(batch)) == receipt
    assert repo.batch("local", batch["batch_id"])["receipt"] == receipt
    assert store.maintenance_epoch("local") == 1


@pytest.mark.parametrize("action", ["retire", "revoke"])
def test_stale_dense_index_cannot_restore_inactive_memory(action: str) -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    source = StructuredMemorySource(store)
    query = AnalyzedQuery("unmatched", "unmatched", "general", None, False, False, ())
    request = SearchRequest("unmatched", "local", "p1", "automatic")

    def stale_hits(limit: int) -> list[VectorHit]:
        return [VectorHit("semantic:a", 0.0)]

    def read(q: AnalyzedQuery) -> list[str]:
        return [
            c.evidence_id
            for c in source._dense_candidates(
                q, request=request, limit=10, search=stale_hits, cancel_check=None
            )
        ]

    assert read(query) == ["semantic:a"]
    repo = store.maintenance_repository()
    repo.commit("local", authorize(prepare(repo, decision("a", action))))
    assert read(query) == []
    assert read(replace(query, include_historical=True)) == (
        ["semantic:a"] if action == "retire" else []
    )
    store.close()


def test_stale_batch_rolls_back_all_changes_and_refresh_requires_new_review() -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    add(store, "b")
    repo = store.maintenance_repository()
    batch = prepare(repo, decision("a"), decision("b"))
    store.supersede_semantic("b", datetime.now(UTC))
    with pytest.raises(MaintenanceError):
        repo.commit("local", authorize(batch))
    assert repo.detail("local", "a")["state"] == "active"
    assert repo.batch("local", batch["batch_id"])["receipt"] is None
    assert store.maintenance_epoch("local") == 0
    status = repo.refresh("local", batch["audit_id"], ["b"])
    assert status["reviewed"] == 1
    with pytest.raises(MaintenanceError, match="review_required"):
        repo.prepare("local", "current", batch["audit_id"], ["b"])


def test_wrong_owner_session_and_digest_cannot_commit() -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    repo = store.maintenance_repository()
    batch = prepare(repo, decision("a"))
    auth = authorize(batch)
    for candidate in (
        replace(auth, batch_digest="different"),
        replace(auth, session_id="other"),
        replace(auth, request_id=""),
    ):
        with pytest.raises(MaintenanceError):
            repo.commit("local", candidate)
    with pytest.raises(MaintenanceError, match="batch_not_found"):
        repo.commit("other", auth)
    assert repo.detail("local", "a")["state"] == "active"


def test_decisions_cannot_claim_unrelated_evidence_and_batch_is_frozen() -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    repo = store.maintenance_repository()
    aid = repo.start("local", "current")["audit_id"]
    with pytest.raises(MaintenanceError, match="invalid_evidence_ref"):
        repo.record("local", aid, [decision("a", evidence_refs=["unrelated#x"])])
    repo.record("local", aid, [decision("a")])
    batch = repo.prepare("local", "current", aid, ["a"])
    assert repo.prepare("local", "current", aid, ["a"])["batch_id"] == batch["batch_id"]
    repo.record("local", aid, [decision("a", "revoke")])
    assert repo.batch("local", batch["batch_id"])["payload"]["operations"][0]["action"] == "retire"


def test_index_failure_does_not_misreport_committed_transaction() -> None:
    store = SqliteMemoryStore(":memory:")
    add(store, "a")

    def failing_index() -> None:
        raise RuntimeError("offline")

    service = SemanticMaintenance(store, owner_user_id="local", request_backfill=failing_index)
    batch = prepare(service.repository, decision("a"))
    result = service.commit(authorize(batch))
    assert result["status"] == "committed"
    assert result["index_refresh"] == "request_failed"
    assert (
        service.repository.batch("local", batch["batch_id"])["receipt"]["index_refresh"]
        == "request_failed"
    )


def test_stale_extraction_cannot_reintroduce_retired_fact() -> None:
    from memory.contracts import ConversationFragment, Utterance
    from memory.extraction.context import ExtractionContextBuilder
    from memory.extraction.reconciler import ExtractionApplyError, Reconciler
    from memory.extraction.result import ExtractionOutput, SemanticChange, SemanticFactDraft

    store = SqliteMemoryStore(":memory:")
    add(store, "a")
    fragment = ConversationFragment(
        session_id="old",
        persona_id="p1",
        utterances=[
            Utterance(
                speaker="user", text="old observation", ts=datetime.now(UTC), source_ref="old#event"
            )
        ],
    )
    context = ExtractionContextBuilder(store).build(fragment)
    repo = store.maintenance_repository()
    repo.commit("local", authorize(prepare(repo, decision("a"))))
    output = ExtractionOutput(
        semantic_changes=[
            SemanticChange(
                new_facts=[SemanticFactDraft(statement="resurrected", reason="old observation")]
            )
        ]
    )
    with pytest.raises(ExtractionApplyError, match="maintenance changed"):
        Reconciler(store).apply(output, fragment, context)
    assert repo.query("local")["items"] == []
