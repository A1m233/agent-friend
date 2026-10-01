"""Regression contracts for requirement 147, using isolated stores and fake models."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memory.contracts import ConversationFragment, Utterance
from memory.core_profile import CoreProfileError
from memory.extraction.context import ExtractionContextBuilder
from memory.extraction.deferred import DeferredObservationError, decode, encode, observation_id
from memory.extraction.extractor import Extractor, _parse_output_with_status
from memory.extraction.reconciler import ExtractionApplyError, Reconciler
from memory.extraction.result import (
    CoreUpdate,
    ExtractionOutput,
    SemanticChange,
    SemanticFactDraft,
    SemanticRetirement,
)
from memory.extraction.worker import AsyncExtractionWorker
from memory.search.contracts import AnalyzedQuery, SearchRequest, TimeRange
from memory.search.structured_source import StructuredMemorySource
from memory.search.vector import VectorHit
from memory.store import SqliteMemoryStore

from llm_providers import LLMStreamEvent, ModelRequest, ModelResponse
from memory import KeywordRetrieval, Memory


def fragment(day: int, session: str = "s") -> ConversationFragment:
    return ConversationFragment(
        session_id=session,
        persona_id="p",
        utterances=[
            Utterance("user", "test fact", datetime(2026, 1, day, tzinfo=UTC), f"{session}#{day}")
        ],
    )


def add_output() -> ExtractionOutput:
    return ExtractionOutput(
        core_updates=[
            CoreUpdate(op="add", field="user.preferred_name", value="Test", reason="explicit")
        ],
        semantic_changes=[
            SemanticChange(
                new_facts=[SemanticFactDraft(statement="works at Acme", reason="explicit")]
            )
        ],
        episodic_entries=["discussed work"],
    )


def apply(store: SqliteMemoryStore, output: ExtractionOutput, source: ConversationFragment) -> None:
    Reconciler(store).apply(output, source, ExtractionContextBuilder(store).build(source))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"unrelated": 1},
        {"core_updates": [], "semantic_changes": []},
        {"core_updates": [], "semantic_changes": [], "episodic_entries": [{}]},
        {"core_updates": [], "semantic_changes": [], "episodic_entries": "wrong"},
        {"semantic_ops": [], "episodic_summary": None},
    ],
)
def test_production_rejects_invalid_or_implicit_legacy_output(payload: object) -> None:
    output, status = _parse_output_with_status(json.dumps(payload))
    assert status == "invalid_shape"
    assert output.validation_error


def test_delayed_observation_keeps_effective_time_and_write_audit() -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    core = store.core_profile(owner_user_id="local", persona_id="p").entries[0]
    fact = store.list_active_ordinary_semantic(owner_user_id="local")[0]
    assert core.valid_from == fact.valid_from == fragment(1).utterances[0].ts
    assert core.created_at > core.valid_from
    apply(
        store,
        ExtractionOutput(
            core_updates=[
                CoreUpdate(
                    op="remove", field="user.preferred_name", target_ref="C001", reason="withdrawn"
                )
            ]
        ),
        fragment(3),
    )
    retired = store.core_profile_entries_by_ids([core.id])[core.id]
    assert retired.valid_until == fragment(3).utterances[0].ts
    assert retired.updated_at > retired.valid_until
    with pytest.raises(ExtractionApplyError, match="observation"):
        apply(store, ExtractionOutput(core_updates=add_output().core_updates), fragment(2))
    assert store.core_profile(owner_user_id="local", persona_id="p").is_empty()
    store.close()


def test_source_invalidation_includes_retired_versions() -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    core = store.core_profile(owner_user_id="local", persona_id="p").entries[0]
    fact = store.list_active_ordinary_semantic(owner_user_id="local")[0]
    store.remove_core_profile_entry(core.id, fragment(3).utterances[0].ts)
    store.supersede_semantic(fact.id, fragment(3).utterances[0].ts)
    result = store.soft_delete_by_source_events(
        session_id="s", event_uuids={"1"}, deleted_at=datetime.now(UTC)
    )
    assert result["core"] == result["semantic"] == 1
    assert store.core_profile_entries_by_ids([core.id])[core.id].deleted_at is not None
    assert store.semantic_by_ids([fact.id])[fact.id].deleted_at is not None
    store.close()


class EmptyModel:
    context_window = 10000

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse('{"core_updates":[],"semantic_changes":[],"episodic_entries":[]}')

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        raise AssertionError("unexpected streaming call")


def test_bad_head_is_preserved_without_poisoning_queue() -> None:
    store = SqliteMemoryStore(":memory:")
    payload = '{"version":999}'
    store.enqueue_extraction("bad", "bad", payload, ["bad#1"], "ready")
    model = EmptyModel()
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))
    worker.submit(fragment(1))
    worker.flush()
    worker.close()
    assert model.calls == 1
    row = store._conn.execute(
        "SELECT * FROM extraction_pending WHERE observation_id='bad'"
    ).fetchone()
    assert row["payload"] == payload
    assert row["state"] == "blocked"
    assert row["error_code"] == "invalid_deferred_observation"
    assert not store.pending_extractions()
    store.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("value_json", "{"),
        ("value_json", "[]"),
        ("provenance", "{}"),
        ("valid_from", "bad-date"),
        ("field_key", "user.unknown"),
    ],
)
def test_core_corruption_has_one_data_error_boundary(column: str, value: str) -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    store._conn.execute(f"UPDATE core_profile_entry SET {column} = ?", (value,))
    store._conn.commit()
    with pytest.raises(CoreProfileError):
        store.core_profile(owner_user_id="local", persona_id="p")
    store.close()


def test_legacy_is_explicit_and_does_not_accept_arbitrary_objects() -> None:
    legacy = '{"semantic_ops":[],"episodic_summary":null}'
    output, status = _parse_output_with_status(legacy, allow_legacy=True)
    assert status == "empty_output" and output.output_contract_version == 1
    for raw in ("{}", '{"semantic_ops":[]}', '{"semantic_ops":{},"episodic_summary":null}'):
        assert _parse_output_with_status(raw, allow_legacy=True)[1] == "invalid_shape"


def test_invalid_output_remains_pending_then_valid_empty_commits_once() -> None:
    class Model(EmptyModel):
        reply = "{}"

        def generate(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(self.reply)

    store = SqliteMemoryStore(":memory:")
    now = [100.0]
    model = Model()
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store), clock=lambda: now[0])
    worker.submit(fragment(1))
    worker.flush()
    assert len(store.pending_extractions()) == 1
    assert store._conn.execute("SELECT COUNT(*) FROM extraction_commit").fetchone()[0] == 0
    now[0] += 61
    model.reply = '{"core_updates":[],"semantic_changes":[],"episodic_entries":[]}'
    worker.submit(fragment(1))
    worker.flush()
    assert not store.pending_extractions()
    now[0] += 61
    worker.submit(fragment(1))
    worker.flush()
    assert model.calls == 2
    assert store._conn.execute("SELECT COUNT(*) FROM extraction_commit").fetchone()[0] == 1
    worker.close()
    store.close()


def test_cross_session_replacement_history_and_late_atomic_rejection() -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    old = store.list_active_ordinary_semantic(owner_user_id="local")[0]
    change = ExtractionOutput(
        semantic_changes=[
            SemanticChange(
                retirements=[SemanticRetirement("M001", "changed employer")],
                new_facts=[SemanticFactDraft("works at Beta", "explicit")],
            )
        ]
    )
    apply(store, change, fragment(3, "other"))
    old = store.semantic_by_ids([old.id])[old.id]
    assert old.valid_until == fragment(3).utterances[0].ts
    assert old.updated_at > old.valid_until
    assert store.search_semantic(
        "Acme",
        owner_user_id="local",
        persona_id="p",
        limit=10,
        include_historical=True,
        until=fragment(2).utterances[0].ts,
    )
    before = store._conn.execute("SELECT COUNT(*) FROM extraction_commit").fetchone()[0]
    with pytest.raises(ExtractionApplyError) as exc:
        apply(store, replace(change, episodic_entries=["must roll back"]), fragment(2, "late"))
    assert exc.value.code == "observation_time_conflict"
    assert store._conn.execute("SELECT COUNT(*) FROM extraction_commit").fetchone()[0] == before
    store.soft_delete_by_source_events(
        session_id="s", event_uuids={"1"}, deleted_at=datetime.now(UTC)
    )
    assert not store.search_semantic(
        "Acme", owner_user_id="local", persona_id="p", limit=10, include_historical=True
    )
    assert store.search_semantic("Beta", owner_user_id="local", persona_id="p", limit=10)
    assert store.semantic_by_ids([old.id])[old.id].valid_until == old.valid_until
    store.close()


def test_max_utterance_time_and_same_observation_many_values() -> None:
    store = SqliteMemoryStore(":memory:")
    source = replace(fragment(1), utterances=[*fragment(3).utterances, *fragment(1).utterances])
    output = ExtractionOutput(
        core_updates=[
            CoreUpdate(op="add", field="user.current_residences", value=value, reason="explicit")
            for value in ("City A", "City B")
        ],
        episodic_entries=["two residences"],
    )
    apply(store, output, source)
    entries = store.core_profile(owner_user_id="local", persona_id="p").entries
    assert len(entries) == 2
    assert all(entry.valid_from == fragment(3).utterances[0].ts for entry in entries)
    assert (
        store._conn.execute("SELECT occurred_at FROM episodic")
        .fetchone()[0]
        .startswith("2026-01-03")
    )
    store.close()


@pytest.mark.parametrize(
    "payload",
    [
        "null",
        "[]",
        "{",
        '{"version":1,"fragment":[]}',
        json.dumps({"version": 1, "fragment": {"session_id": 42}}),
    ],
)
def test_deferred_codec_classifies_only_data_errors(payload: str) -> None:
    with pytest.raises(DeferredObservationError):
        decode(payload)
    assert decode(encode(fragment(1))) == fragment(1)


def test_blocked_records_survive_migration_restart_and_respect_batch_limit(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SqliteMemoryStore(path)
    # Recreate the real v7 table rather than just changing its version label.
    store._conn.execute("DROP TABLE extraction_pending")
    store._conn.execute(
        "CREATE TABLE extraction_pending (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
        "observation_id TEXT UNIQUE, session_id TEXT, payload TEXT, source_refs TEXT, reason TEXT)"
    )
    store._conn.execute("UPDATE schema_meta SET value='7' WHERE key='schema_version'")
    store._conn.commit()
    for i in range(2):
        store.enqueue_extraction(f"bad{i}", "bad", '{"version":999}', [f"bad#{i}"], "ready")
    store.close()
    store = SqliteMemoryStore(path)
    model = EmptyModel()
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))
    worker.submit(fragment(1))
    worker.flush()
    assert model.calls == 0  # Two bad records consume this activity's two-item budget.
    worker.close()
    store.close()
    store = SqliteMemoryStore(path)
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))
    worker.flush()
    assert model.calls == 0
    worker.submit(fragment(1))
    worker.flush()
    assert model.calls == 1
    store.defer_extractions(100.0, "service_failure")
    rows = store._conn.execute("SELECT state,error_code FROM extraction_pending").fetchall()
    assert [(row[0], row[1]) for row in rows] == [("blocked", "invalid_deferred_observation")] * 2
    assert worker.invalidate("bad", {"0"}) == 1
    assert worker.invalidate("bad") == 1
    worker.close()
    store.close()


@pytest.mark.parametrize(
    "column,value",
    [
        ("value_json", "[]"),
        ("field_key", "unknown"),
        ("scope_kind", "unknown"),
        ("valid_from", "bad"),
        ("provenance", "{}"),
    ],
)
def test_corrupt_core_keeps_ordinary_recall_and_defers_shared_failure(
    column: str, value: str
) -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    store._conn.execute(f"UPDATE core_profile_entry SET {column}=?", (value,))
    store._conn.commit()
    model = EmptyModel()
    memory = Memory(store, Extractor(model), Reconciler(store), retrieval=KeywordRetrieval(store))
    context = memory.retrieve("Acme", persona_id="p")
    assert "Core Profile 暂不可用" in context.rendered
    assert "Acme" in context.rendered
    assert all(item.layer != "core" for item in context.items)
    memory.observe(fragment(2))
    memory.flush()
    assert model.calls == 0
    assert len(store.pending_extractions()) == 1
    memory.close()


@pytest.mark.parametrize("importance", [True, float("nan"), float("inf"), "high"])
def test_wrong_numeric_item_type_rejects_entire_proposal(importance: object) -> None:
    payload = {
        "core_updates": [],
        "episodic_entries": ["must not commit"],
        "semantic_changes": [
            {
                "retirements": [],
                "new_facts": [
                    {"statement": "fact", "reason": "explicit", "importance": importance}
                ],
            }
        ],
    }
    output, status = _parse_output_with_status(json.dumps(payload))
    assert status == "invalid_shape" and output.validation_error


def test_time_conflict_is_blocked_and_next_item_can_commit() -> None:
    class Model(EmptyModel):
        def generate(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                json.dumps(
                    {
                        "core_updates": [
                            {
                                "op": "replace",
                                "field": "user.preferred_name",
                                "target_ref": "C001",
                                "value": "New name",
                                "reason": "explicit",
                            }
                        ],
                        "semantic_changes": [],
                        "episodic_entries": [],
                    }
                )
            )

    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(3))
    model = Model()
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))
    with worker._processing:
        worker.submit(fragment(1, "late"))
        worker.submit(fragment(5, "fresh"))
    worker.flush()
    row = store._conn.execute("SELECT state,error_code FROM extraction_pending").fetchone()
    assert tuple(row) == ("blocked", "observation_time_conflict")
    profile = store.core_profile(owner_user_id="local", persona_id="p")
    assert profile.entries[0].value == "New name"
    assert profile.entries[0].valid_from == fragment(5).utterances[0].ts
    assert model.calls == 2
    worker.close()
    store.close()


def test_stale_dense_hit_cannot_reintroduce_invalidated_history() -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(1))
    fact = store.list_active_ordinary_semantic(owner_user_id="local")[0]
    store.supersede_semantic(fact.id, fragment(3).utterances[0].ts)
    query = AnalyzedQuery(
        "Acme",
        "Acme",
        "historical_state",
        TimeRange(until=fragment(2).utterances[0].ts),
        False,
        True,
        ("Acme",),
    )
    request = SearchRequest("Acme", "local", "p", "explicit")
    source = StructuredMemorySource(store)

    def dense_hits(limit: int) -> list[VectorHit]:
        return [VectorHit(f"semantic:{fact.id}", 0.01)]

    assert source._dense_candidates(
        query, request=request, limit=10, search=dense_hits, cancel_check=None
    )
    store.soft_delete_by_source_events(
        session_id="s", event_uuids={"1"}, deleted_at=datetime.now(UTC)
    )
    assert not source._dense_candidates(
        query, request=request, limit=10, search=dense_hits, cancel_check=None
    )
    store.close()


def test_core_infrastructure_errors_are_not_data_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SqliteMemoryStore(":memory:")
    memory = Memory(
        store, Extractor(EmptyModel()), Reconciler(store), retrieval=KeywordRetrieval(store)
    )

    def unavailable(**kwargs: object) -> None:
        raise sqlite3.OperationalError("synthetic I/O failure")

    monkeypatch.setattr(store, "core_profile", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        memory.retrieve("Acme", persona_id="p")
    memory.close()


def test_identity_corruption_is_blocked_before_model_call() -> None:
    store = SqliteMemoryStore(":memory:")
    source = fragment(1)
    store.enqueue_extraction("mismatched", "s", encode(source), ["s#1"], "ready")
    model = EmptyModel()
    worker = AsyncExtractionWorker(store, Extractor(model), Reconciler(store))
    worker._drain(store.pending_extraction_sequence())
    assert model.calls == 0
    assert store._conn.execute("SELECT state FROM extraction_pending").fetchone()[0] == "blocked"
    worker.close()
    store.close()


def test_blocking_time_conflict_does_not_bypass_service_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteMemoryStore(":memory:")
    apply(store, add_output(), fragment(3))
    extractor = Extractor(EmptyModel())
    calls: list[str] = []

    def degraded(
        source: ConversationFragment, context: object, *, allow_retries: bool
    ) -> ExtractionOutput:
        calls.append(source.session_id)
        return ExtractionOutput(
            core_updates=[CoreUpdate("replace", "user.preferred_name", "explicit", "New", "C001")],
            reconciliation_degraded=True,
            retry_after_seconds=3600,
        )

    monkeypatch.setattr(extractor, "extract", degraded)
    worker = AsyncExtractionWorker(store, extractor, Reconciler(store), clock=lambda: 100.0)
    for source in (fragment(1, "late"), fragment(5, "fresh")):
        store.enqueue_extraction(
            observation_id(source),
            source.session_id,
            encode(source),
            [u.source_ref for u in source.utterances],
            "ready",
        )
    worker._drain(store.pending_extraction_sequence())
    assert calls == ["late"]
    assert store.extraction_cooldown() == 3700.0
    assert len(store.pending_extractions()) == 1
    worker.close()
    store.close()
