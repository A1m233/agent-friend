from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory.contracts import ConversationFragment, Utterance
from memory.extraction.extractor import Extractor
from memory.extraction.reconciler import Reconciler
from memory.extraction.worker import AsyncExtractionWorker
from memory.store import SqliteMemoryStore

from llm_providers import LLMNetworkError, LLMTransientProviderError, ModelRequest, ModelResponse


class Model:
    context_window = 10000

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.failed = False

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.failed:
            raise LLMNetworkError("synthetic outage")
        return ModelResponse('{"core_updates":[],"semantic_changes":[],"episodic_entries":[]}')


def fragment(index: int, *, failed: bool = False) -> ConversationFragment:
    return ConversationFragment(
        session_id="session",
        persona_id="persona",
        defer_extraction=failed,
        utterances=[Utterance("user", f"new fact {index}", datetime.now(UTC), f"session#{index}")],
    )


def worker(store: SqliteMemoryStore, model: Any, now: list[float]) -> AsyncExtractionWorker:
    return AsyncExtractionWorker(store, Extractor(model), Reconciler(store), clock=lambda: now[0])


def test_failed_turns_survive_restart_and_resume_at_most_two(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SqliteMemoryStore(path)
    model = Model()
    now = [100.0]
    first = worker(store, model, now)
    for i in range(5):
        first.submit(fragment(i, failed=True))
    first.flush()
    first.close()
    assert model.requests == []
    store.close()
    store = SqliteMemoryStore(path)
    resumed = worker(store, model, now)
    resumed.flush()
    assert model.requests == []
    resumed.submit(fragment(5))
    resumed.flush()
    assert len(model.requests) == 2
    assert all(not request.control.allow_retries for request in model.requests)
    assert len(store.pending_extractions(100)) == 4
    resumed.submit(fragment(6))
    resumed.flush()
    assert len(model.requests) == 2
    now[0] += 61
    resumed.submit(fragment(7))
    resumed.flush()
    assert len(model.requests) == 4
    resumed.close()
    store.close()


def test_failure_is_not_committed_and_repeated_activity_cannot_burst() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model()
    model.failed = True
    now = [100.0]
    active = worker(store, model, now)
    active.submit(fragment(0))
    active.flush()
    for i in range(1, 10):
        active.submit(fragment(i))
        active.flush()
    assert len(model.requests) == 1
    assert len(store.pending_extractions(100)) == 10
    model.failed = False
    now[0] += 61
    active.submit(fragment(10))
    active.flush()
    assert len(model.requests) == 3
    assert "new fact 0" in model.requests[1].messages[1]["content"]
    active.close()
    store.close()


def test_invalidated_and_duplicate_sources_do_not_regenerate() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model()
    now = [100.0]
    active = worker(store, model, now)
    active.submit(fragment(0, failed=True))
    assert active.invalidate("session", {"0"}) == 1
    active.submit(fragment(1))
    active.flush()
    assert len(model.requests) == 1
    active.submit(fragment(1))
    active.flush()
    assert len(model.requests) == 1
    active.submit(fragment(2, failed=True))
    active.invalidate("session")
    active.submit(replace(fragment(3), utterances=[]))
    active.flush()
    assert len(model.requests) == 1
    active.close()
    store.close()


def test_old_activity_cannot_release_a_later_failed_turn() -> None:
    store = SqliteMemoryStore(":memory:")
    model = Model()
    active = worker(store, model, [100.0])
    with active._processing:
        active.submit(fragment(0))
        active.submit(fragment(1, failed=True))
    active.flush()
    assert len(model.requests) == 1
    assert len(store.pending_extractions()) == 1
    active.close()
    store.close()


def test_deferred_work_respects_longer_server_cooldown() -> None:
    class CooldownModel(Model):
        def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            raise LLMTransientProviderError("synthetic overload", retry_after_seconds=3600)

    store = SqliteMemoryStore(":memory:")
    model = CooldownModel()
    now = [100.0]
    active = worker(store, model, now)
    active.submit(fragment(0))
    active.flush()
    now[0] += 61
    active.submit(fragment(1))
    active.flush()
    assert len(model.requests) == 1
    assert store.extraction_cooldown() == 3700.0
    active.close()
    store.close()
