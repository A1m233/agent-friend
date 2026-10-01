from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event as ThreadEvent
from types import SimpleNamespace
from typing import cast

import pytest
from agent.memory_search import SessionHistorySource, SessionStoreSourceOrderProvider
from agent.runtime.cancellation import CancellationToken, CancelReason, RunCancelled
from agent.sessions import JsonlSessionStore, Session, SessionCorruptError, SessionNotFoundError
from agent.sessions.events import Event, EventType
from agent.sessions.metadata import SessionMetadataStore
from agent.sessions.query import CancelCheck
from agent.sessions.recall import query_session_recall
from agent.sessions.store import SessionStore
from agent.sessions.store_types import SessionReadState
from agent.tools.builtin.conversation_history.tool import ConversationHistoryTool
from memory.search import QueryAnalyzer, SearchRequest, TimeRange
from memory.search.contracts import SearchMode

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def event(kind: EventType, uuid: str, **payload: object) -> Event:
    return Event(type=kind, uuid=uuid, ts=NOW, payload=payload)


def store_with_history(root: Path, sid: str = "one") -> JsonlSessionStore:
    store = JsonlSessionStore(root, query_cache_entries=0, query_cache_bytes=0)
    store.create(
        Session.new(
            "test", "default", "test/model", session_id=sid, created_at=NOW - timedelta(days=1)
        )
    )
    store.append_events(
        sid,
        [
            event("user_message", "u1", content="学日语 Straße x"),
            event("model_transcript_item", "rollout", message={"content": "PRIVATE" * 5000}),
            event("tool_call_result", "tool", content="PRIVATE_TOOL" * 5000),
            event("assistant_message", "a1", content="影子跟读"),
            event("user_message", "u2", content="今天的当前轮"),
            event("assistant_message", "a2", content="还没有完成", partial=True),
        ],
    )
    return store


def no_scan(*args: object, **kwargs: object) -> None:
    raise AssertionError("warm recall must not scan the JSONL")


def test_warm_alternating_append_and_restart_do_not_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_with_history(tmp_path)
    store_with_history(tmp_path, "two")
    for sid in ("one", "two"):
        query_session_recall(store, sid)
    # No business snapshot cache is available. Recreate the store as well.
    fresh = JsonlSessionStore(tmp_path, query_cache_bytes=0)
    monkeypatch.setattr(fresh, "iter_events", no_scan)
    for sid in ("two", "one", "two", "one"):
        snapshot = query_session_recall(fresh, sid)
        assert [e.uuid for e in snapshot.events] == ["u1", "a1", "u2"]
        assert snapshot.raw_event_indices["a1"] == 4
        assert snapshot.raw_event_count == 7
    fresh.append_events(
        "one",
        [
            event("model_transcript_item", "rollout2", message={}),
            event("assistant_message", "a3", content="追加答案"),
        ],
    )
    assert query_session_recall(fresh, "one").raw_event_indices["a3"] == 8
    assert query_session_recall(fresh, "one").events[-1].uuid == "a3"
    # Another independent writer maintains the same projection.
    JsonlSessionStore(tmp_path).append_event("two", event("user_message", "u3", content="new"))
    assert query_session_recall(fresh, "two").events[-1].uuid == "u3"
    for path in (tmp_path / ".recall").glob("*.sqlite3"):
        assert b"PRIVATE_TOOL" not in path.read_bytes()
        assert b"PRIVATEPRIVATE" not in path.read_bytes()


def test_incremental_rewrite_preserves_inactive_targets_and_snapshot_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_with_history(tmp_path)
    previous = query_session_recall(store, "one")
    monkeypatch.setattr(store, "iter_events", no_scan)
    store.append_events(
        "one",
        [
            event("turn_rewrite", "rewrite", inactive_event_uuids=["u2", "a2", "future"]),
            event("user_message", "u3", content="edited"),
            event("assistant_message", "future", content="must stay inactive"),
        ],
    )
    current = query_session_recall(store, "one")
    assert [e.uuid for e in previous.events] == ["u1", "a1", "u2"]
    assert [e.uuid for e in current.events] == ["u1", "a1", "u3"]
    assert current.raw_event_count == 10


@pytest.mark.parametrize("change", ["append", "replace", "truncate", "delete"])
def test_external_source_mutation_never_serves_old_projection(tmp_path: Path, change: str) -> None:
    store = store_with_history(tmp_path)
    query_session_recall(store, "one")
    path = tmp_path / "one.jsonl"
    if change == "append":
        with path.open("ab") as handle:
            handle.write(
                event("user_message", "external", content="new").to_jsonl().encode() + b"\n"
            )
        assert query_session_recall(store, "one").events[-1].uuid == "external"
    elif change == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
        replacement.replace(path)
        assert query_session_recall(store, "one").events == ()
    elif change == "truncate":
        path.write_bytes(b"broken")
        with pytest.raises(SessionCorruptError):
            query_session_recall(store, "one")
    else:
        path.unlink()
        with pytest.raises(SessionNotFoundError):
            query_session_recall(store, "one")


@pytest.mark.parametrize("damage", ["corrupt", "schema", "missing_table", "invalid_row"])
def test_derived_damage_rebuilds_without_changing_source(tmp_path: Path, damage: str) -> None:
    store = store_with_history(tmp_path)
    expected = query_session_recall(store, "one")
    original = (tmp_path / "one.jsonl").read_bytes()
    path = store._recall_index.path("one")
    if damage == "corrupt":
        path.write_bytes(b"not a database")
    else:
        with sqlite3.connect(path) as db:
            if damage == "schema":
                db.execute("PRAGMA user_version=999")
            elif damage == "missing_table":
                db.execute("DROP TABLE positions")
            else:
                db.execute("UPDATE utterances SET ts='not a timestamp'")
    assert query_session_recall(store, "one") == expected
    assert (tmp_path / "one.jsonl").read_bytes() == original


def test_failed_increment_rolls_back_without_reporting_durable_append_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_with_history(tmp_path)
    before = query_session_recall(store, "one")
    insert = store._recall_index._insert_event

    def fail_after_insert(db: sqlite3.Connection, position: int, item: Event) -> None:
        insert(db, position, item)
        raise sqlite3.OperationalError("simulated disk failure")

    monkeypatch.setattr(store._recall_index, "_insert_event", fail_after_insert)
    store.append_event("one", event("user_message", "durable", content="persisted once"))
    assert [e.uuid for e in store.load("one").events].count("durable") == 1
    assert before.source_version is not None
    assert store._recall_index.read("one", before.source_version) == before
    monkeypatch.setattr(store._recall_index, "_insert_event", insert)
    assert query_session_recall(store, "one").events[-1].uuid == "durable"


def test_unwritable_projection_uses_validated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_with_history(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("read only derived directory")

    monkeypatch.setattr(store._recall_index, "publish", fail)
    assert query_session_recall(store, "one").events[-1].uuid == "u2"


def test_cancelled_build_is_not_published_and_cancelled_hit_is_not_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_with_history(tmp_path)
    token = CancellationToken()
    original = store.iter_events

    def cancelling(
        sid: str, state: SessionReadState, *, cancel_check: CancelCheck = None
    ) -> Iterator[Event]:
        for item in original(sid, state, cancel_check=cancel_check):
            yield item
            token.cancel(CancelReason.EXPLICIT_REQUEST)

    monkeypatch.setattr(store, "iter_events", cancelling)
    with pytest.raises(RunCancelled):
        query_session_recall(store, "one", cancel_check=token.raise_if_cancelled)
    assert not store._recall_index.path("one").exists()
    monkeypatch.setattr(store, "iter_events", original)
    expected = query_session_recall(store, "one")
    with pytest.raises(RunCancelled):
        query_session_recall(store, "one", cancel_check=token.raise_if_cancelled)
    monkeypatch.setattr(store, "iter_events", no_scan)
    assert query_session_recall(store, "one") == expected


def test_source_appends_and_other_sessions_progress_during_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_with_history(tmp_path)
    store_with_history(tmp_path, "two")
    entered, release = ThreadEvent(), ThreadEvent()
    parse = Event.from_jsonl

    def blocked(line: str) -> Event:
        if '"u1"' in line and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return parse(line)

    monkeypatch.setattr(Event, "from_jsonl", blocked)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(query_session_recall, store, "one")
        assert entered.wait(5)
        try:
            pool.submit(
                store.append_event, "one", event("user_message", "new", content="new")
            ).result(2)
            assert pool.submit(query_session_recall, store, "two").result(2).session_id == "two"
        finally:
            release.set()
        pinned = first.result(5)
    assert pinned.events[-1].uuid == "u2"
    assert query_session_recall(store, "one").events[-1].uuid == "new"


@pytest.mark.parametrize(
    "text,mode",
    [
        ("日语", "automatic"),
        ("影子", "explicit"),
        ("x", "explicit"),
        ("STRASSE", "explicit"),
        ("", "explicit"),
    ],
)
def test_recall_candidates_match_business_projection(
    tmp_path: Path, text: str, mode: SearchMode
) -> None:
    store = store_with_history(tmp_path)
    store_with_history(tmp_path, "two")
    store.append_events(
        "one",
        [
            event("turn_rewrite", "rewrite", inactive_event_uuids=["u2", "a2"]),
            Event(
                type="user_message",
                uuid="other-persona",
                ts=NOW,
                payload={"content": "日语 x"},
                meta={"persona_id": "other"},
            ),
        ],
    )
    oracle = cast(
        SessionStore, SimpleNamespace(query=store.query, list_activity=store.list_activity)
    )
    request = SearchRequest(
        text=text,
        owner_user_id="local",
        persona_id="p1",
        mode=mode,
        explicit_time_range=TimeRange(since=NOW - timedelta(days=1), until=NOW + timedelta(days=1)),
    )
    query = QueryAnalyzer(lambda: NOW).analyze(request)
    actual = SessionHistorySource(store).search(query, request=request, limit=10)
    assert actual
    assert actual == SessionHistorySource(oracle).search(query, request=request, limit=10)
    assert SessionStoreSourceOrderProvider(store).active_conversation_event_uuids("one") == (
        SessionStoreSourceOrderProvider(oracle).active_conversation_event_uuids("one")
    )


def test_uuid_turn_boundary_wins_over_execution_list_position(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    # In a compact execution list a1 is index 3 and u2 is index 4;
    # their raw positions are 4 and 5. Comparing 4 directly excludes a1 incorrectly.
    request = SearchRequest(
        text="影子",
        owner_user_id="local",
        persona_id="",
        mode="explicit",
        session_id="one",
        current_turn_start_index=4,
        current_turn_start_event_uuid="u2",
    )
    query = QueryAnalyzer(lambda: NOW).analyze(request)
    result = SessionHistorySource(store).search(query, request=request, limit=10)
    assert len(result) == 1
    assert result[0].provenance == ("one#u1", "one#a1")


def test_delete_removes_derived_content(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    query_session_recall(store, "one")
    path = store._recall_index.path("one")
    store.delete("one")
    assert not path.exists()
    with pytest.raises(SessionNotFoundError):
        query_session_recall(store, "one")


def test_cancelled_publication_rolls_back_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_with_history(tmp_path)
    token = CancellationToken()
    insert = store._recall_index._insert_event

    def cancel_after_insert(db: sqlite3.Connection, position: int, item: Event) -> None:
        insert(db, position, item)
        token.cancel(CancelReason.EXPLICIT_REQUEST)

    monkeypatch.setattr(store._recall_index, "_insert_event", cancel_after_insert)
    with pytest.raises(RunCancelled):
        query_session_recall(store, "one", cancel_check=token.raise_if_cancelled)
    monkeypatch.setattr(store._recall_index, "_insert_event", insert)
    assert query_session_recall(store, "one").events[-1].uuid == "u2"


def test_waiting_reader_can_cancel_without_invalidating_shared_projection(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    expected = query_session_recall(store, "one")
    token = CancellationToken()
    entered = ThreadEvent()

    def check() -> None:
        entered.set()
        token.raise_if_cancelled()

    path = store._recall_index.path("one")
    with store._recall_index._locked(path, None), ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(query_session_recall, store, "one", cancel_check=check)
        assert entered.wait(2)
        token.cancel(CancelReason.EXPLICIT_REQUEST)
        with pytest.raises(RunCancelled):
            waiting.result(2)
    assert query_session_recall(store, "one") == expected


def test_separate_process_reuses_and_advances_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_with_history(tmp_path)
    query_session_recall(store, "one")
    script = """
import sys
from datetime import datetime, UTC
from agent.sessions import JsonlSessionStore, Event
store = JsonlSessionStore(sys.argv[1])
def no_scan(*args, **kwargs):
    raise AssertionError('persistent projection was not reused')
store.iter_events = no_scan
assert store.query_recall('one').events[-1].uuid == 'u2'
store.append_event('one', Event(type='user_message', uuid='process', ts=datetime.now(UTC), payload={'content': 'new process'}))
assert store.query_recall('one').events[-1].uuid == 'process'
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr(store, "iter_events", no_scan)
    assert query_session_recall(store, "one").events[-1].uuid == "process"


def test_explicit_tool_uses_uuid_boundary_and_does_not_expose_hidden_fields(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    tool = ConversationHistoryTool(store, clock=lambda: NOW)
    result = tool.invoke(
        {
            "search_text": "影子",
            "__agent_friend_current_session_id": "one",
            "__agent_friend_current_turn_start_index": 4,
            "__agent_friend_current_turn_start_event_uuid": "u2",
        }
    )
    assert not result.is_error
    assert "影子跟读" in result.text
    assert "今天的当前轮" not in result.text
    assert (
        "__agent_friend_current_turn_start_event_uuid" not in tool.parameters_schema["properties"]
    )


def test_archive_preserves_recall_and_missing_uuid_boundary_does_not_widen_scope(
    tmp_path: Path,
) -> None:
    store = store_with_history(tmp_path)
    tool = ConversationHistoryTool(store, clock=lambda: NOW)
    expected = tool.invoke({"search_text": "影子"})
    SessionMetadataStore(tmp_path / "metadata.json").set_archived_at("one", NOW)
    assert tool.invoke({"search_text": "影子"}) == expected
    result = tool.invoke(
        {
            "search_text": "影子",
            "__agent_friend_current_session_id": "one",
            "__agent_friend_current_turn_start_index": 99,
            "__agent_friend_current_turn_start_event_uuid": "missing-current-turn",
        }
    )
    assert result.meta is not None and result.meta["result_count"] == 0


def test_sql_progress_cancellation_preserves_original_exception(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    query_session_recall(store, "one")
    armed = False
    cancelled = ValueError("original cancellation")

    def cancel() -> None:
        if armed:
            raise cancelled

    path = store._recall_index.path("one")
    with (
        pytest.raises(ValueError) as caught,
        store._recall_index._locked(path, None),
        store._recall_index._database(path, cancel) as db,
    ):
        armed = True
        db.execute(
            "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT SUM(x) FROM n"
        ).fetchone()
    assert caught.value is cancelled
    assert query_session_recall(store, "one").events[-1].uuid == "u2"


def test_one_shot_io_shaped_cancellation_is_not_treated_as_storage_failure(tmp_path: Path) -> None:
    store = store_with_history(tmp_path)
    query_session_recall(store, "one")
    count = 0
    cancelled = InterruptedError("one-shot cancellation")

    def cancel_once() -> None:
        nonlocal count
        count += 1
        if count == 5:
            raise cancelled

    with pytest.raises(InterruptedError) as caught:
        query_session_recall(store, "one", cancel_check=cancel_once)
    assert caught.value is cancelled
