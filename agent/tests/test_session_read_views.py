from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as Signal

import pytest
from agent.sessions import store as store_module
from agent.sessions.events import EventType
from agent.sessions.preparation import ExecutionPreparation

from agent import Event, JsonlSessionStore, Session, SessionManager


def event(event_type: EventType, uid: str, **payload: object) -> Event:
    return Event(event_type, uid, datetime.now(UTC), payload)


def history(root: Path, turns: int = 65) -> JsonlSessionStore:
    store = JsonlSessionStore(root)
    store.create(Session.new("History", "default", "test/model", session_id="one"))
    store.append_events(
        "one",
        [
            item
            for i in range(turns)
            for item in (
                event("user_message", f"u{i}", content=f"question {i}"),
                event("assistant_message", f"a{i}", content=f"answer {i}"),
            )
        ],
    )
    return store


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("interactive reads must not hydrate or rebuild full history")


@pytest.mark.parametrize("legacy", [False, True])
def test_first_ten_then_thirty_without_full_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    store = history(tmp_path)
    expected = store.load("one").active_events
    if legacy:
        store.discard_business_index("one")
    monkeypatch.setattr(store, "query", forbidden)
    monkeypatch.setattr(store, "_read_records", forbidden)
    first = store.history_window("one", turn_limit=10)
    assert [e.uuid for e in first.active_events if e.type == "user_message"] == [
        f"u{i}" for i in range(55, 65)
    ]
    collected = list(first.active_events)
    page = first
    while page.history_cursor:
        page = store.history_window(
            "one", turn_limit=30, before=page.history_cursor, history_epoch=page.history_epoch
        )
        collected[:0] = page.active_events
    assert collected == expected


def test_legacy_rewrite_and_internal_payload_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path, 12)
    store.append_events(
        "one",
        [
            event("turn_rewrite", "rewrite", inactive_event_uuids=["u11", "a11"]),
            event("user_message", "replacement", content="edited"),
            event("rollout_value", "large", value="x" * 2_000_000),
        ],
    )
    store.discard_business_index("one")
    original = Event.from_jsonl

    def decode(line: str) -> Event:
        assert '"large"' not in line
        return original(line)

    monkeypatch.setattr(Event, "from_jsonl", decode)
    page = store.history_window("one", turn_limit=10)
    assert page.active_events[-1].uuid == "replacement"
    assert not {"u11", "a11"} & {e.uuid for e in page.active_events}
    older = store.history_window(
        "one", turn_limit=30, before=page.history_cursor, history_epoch=page.history_epoch
    )
    assert (
        len([e for e in (*older.active_events, *page.active_events) if e.type == "user_message"])
        == 12
    )


def test_legacy_block_edge_and_noncanonical_last_record(tmp_path: Path) -> None:
    store = history(tmp_path, turns=12)
    store.discard_business_index("one")
    path = store._path("one")
    # Place a block edge in the middle of the type prefix, and put type last
    # on the unterminated final line, as accepted by the historical JSON reader.
    internal = event("rollout_value", "internal", data="").to_jsonl()
    last = json.loads(event("user_message", "last", content="final").to_jsonl())
    last["type"] = last.pop("type")
    suffix = json.dumps(last).encode()
    size = 256 * 1024 - len(internal.encode()) - len(suffix) + 7
    with path.open("ab") as stream:
        stream.write(event("rollout_value", "internal", data="x" * size).to_jsonl().encode())
        stream.write(b"\n" + suffix)
    page = store.history_window("one", turn_limit=10)
    assert [e.uuid for e in page.active_events if e.type == "user_message"] == [
        *(f"u{i}" for i in range(3, 12)),
        "last",
    ]


def test_composer_and_config_append_never_query_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    monkeypatch.setattr(store, "query", forbidden)
    monkeypatch.setattr(store, "_read_records", forbidden)
    monkeypatch.setattr(store._business_index, "_snapshot", forbidden)
    assert store.composer_session("one").current_model == "test/model"
    store.append_event("one", event("model_change", "model", to="deepseek/deepseek-chat"))
    assert (
        JsonlSessionStore(tmp_path).composer_session("one").current_model
        == "deepseek/deepseek-chat"
    )


def test_origin_check_does_not_enumerate_other_session_histories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    monkeypatch.setattr(store, "list", forbidden)
    monkeypatch.setattr(store, "load", forbidden)
    monkeypatch.setattr(store, "query", forbidden)
    manager = SessionManager(store)
    try:
        assert manager.is_inner("one") is False
        store.create(
            Session.new(
                "inner", "default", "test/model", session_id="inner", origin_kind="inner_world"
            )
        )
        assert manager.is_inner("inner") is True
    finally:
        manager.close()


def test_sidebar_summary_reuses_configuration_without_decoding_old_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    # Bootstrap a legacy source by invalidating the small derived configuration.
    store._configuration.delete("one")
    original = Event.from_jsonl

    def decode(line: str) -> Event:
        result = original(line)
        if result.type in {"user_message", "assistant_message"}:
            assert result.uuid == "a64"  # only the newest timestamp is needed
        return result

    monkeypatch.setattr(Event, "from_jsonl", decode)
    assert store.list()[0].model == "test/model"


def test_page_does_not_wait_on_index_maintenance_lock(tmp_path: Path) -> None:
    store = history(tmp_path)
    with (
        store._business_index._locked(store._business_index.path("one").with_suffix(".lock"), None),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        assert (
            executor.submit(store.history_window, "one", turn_limit=10)
            .result(timeout=1)
            .active_events[-1]
            .uuid
            == "a64"
        )


def test_sidebar_and_startup_reads_leave_unopened_legacy_chats_unindexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.sessions.recovery import ToolTransactionReconciler
    from agent.sessions.rollout_encoding import fingerprint

    value = "x" * 1_048_576
    for sid in ("one", "two", "three"):
        session = Session.new(sid, "default", "test/model", session_id=sid)
        session.events.extend(
            [
                event("user_message", "user", content="visible"),
                event(
                    "rollout_value",
                    "internal",
                    encoding_version=1,
                    kind="model_context",
                    sha256=fingerprint(value),
                    value=value,
                ),
            ]
        )
        (tmp_path / f"{sid}.jsonl").write_text(
            "\n".join(item.to_jsonl() for item in session.events) + "\n",
            encoding="utf-8",
        )
    store = JsonlSessionStore(tmp_path)
    monkeypatch.setattr(store_module, "read_configuration", forbidden)
    decode = Event.from_jsonl
    with monkeypatch.context() as scoped:

        def no_internal_body(line: str) -> Event:
            assert "rollout_value" not in line
            return decode(line)

        scoped.setattr(Event, "from_jsonl", no_internal_body)
        manager = SessionManager(store)
        try:
            rows = manager.list(navigation=True)
            assert len(rows) == 3 and all(row.updated_at >= row.created_at for row in rows)
        finally:
            manager.close()
    restarted = JsonlSessionStore(tmp_path)
    monkeypatch.setattr(store_module, "latest_timestamp", forbidden)
    assert restarted.list_navigation() == rows
    results = ToolTransactionReconciler(
        restarted, runtime_instance_id="fresh"
    ).reconcile_known_sessions()
    assert len(results) == 3 and not any(item.failed for item in results)
    assert not list((tmp_path / ".configuration").glob("*.json"))
    assert not list((tmp_path / ".business").glob("*.sqlite3"))


def test_changed_source_rejects_legacy_cursor(tmp_path: Path) -> None:
    store = history(tmp_path)
    store.discard_business_index("one")
    first = store.history_window("one", turn_limit=10)
    store.append_event("one", event("user_message", "new", content="new"))
    with pytest.raises(ValueError, match="History changed"):
        store.history_window(
            "one", turn_limit=30, before=first.history_cursor, history_epoch=first.history_epoch
        )


@pytest.mark.parametrize("legacy", [True, False])
def test_display_rows_survive_restart_and_follow_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    store = history(tmp_path)
    marker = "private-accounting-diagnostic"
    tool_text = "full tool result " * 512
    tool = Event(
        "tool_call_result",
        "tool",
        datetime.now(UTC),
        {"content": tool_text},
        {"context_usage": marker, "run_id": "run"},
    )
    reasoning = event("provider_reasoning_state", "reasoning", data="provider secret")
    store.append_events("one", [tool, reasoning])
    if legacy:
        store.discard_business_index("one")
    first = store.history_window("one", turn_limit=10)
    older = store.history_window(
        "one", turn_limit=30, before=first.history_cursor, history_epoch=first.history_epoch
    )
    assert first.active_events[-2].payload["content"] == tool_text
    assert first.active_events[-2].meta == {"run_id": "run"}
    assert first.active_events[-1].payload == {"redacted": True, "dataCharCount": 15}
    assert marker in store._path("one").read_text(encoding="utf-8")
    monkeypatch.setattr(store_module, "read_window", forbidden)
    decode = Event.from_jsonl

    def display_only(line: str) -> Event:
        assert marker not in line and "provider secret" not in line
        return decode(line)

    fresh = JsonlSessionStore(tmp_path)
    with monkeypatch.context() as checks:
        checks.setattr(Event, "from_jsonl", display_only)
        assert fresh.history_window("one", turn_limit=10).active_events == first.active_events
        assert (
            fresh.history_window(
                "one", turn_limit=30, before=first.history_cursor, history_epoch=first.history_epoch
            ).active_events
            == older.active_events
        )
    fresh.append_event("one", event("user_message", "new-turn", content="new"))
    updated = fresh.history_window("one", turn_limit=10)
    assert updated.active_events[-1].uuid == "new-turn"
    assert sum(e.type == "user_message" for e in updated.active_events) == 10


def test_compatible_old_index_backfills_only_opened_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    with sqlite3.connect(store._business_index.path("one")) as db:
        db.execute("DROP TABLE chat_bodies")
    monkeypatch.setattr(store_module, "read_window", forbidden)
    page = store.history_window("one", turn_limit=10)
    with sqlite3.connect(store._business_index.path("one")) as db:
        assert {row[0] for row in db.execute("SELECT uuid FROM chat_bodies")} == {
            event.uuid for event in page.active_events
        }


def test_partial_legacy_cache_rewrite_and_failed_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path)
    store.discard_business_index("one")
    with store._business_index._locked(
        store._business_index.path("one").with_suffix(".lock"), None
    ):
        assert store.history_window("one", turn_limit=10).active_events[-1].uuid == "a64"
    assert not store._business_index.path("one").exists()
    store.history_window("one", turn_limit=10)
    store.append_events(
        "one",
        [
            event("turn_rewrite", "r", inactive_event_uuids=["u64", "a64"]),
            event("user_message", "replaced", content="replacement"),
        ],
    )
    first = store.history_window("one", turn_limit=10)
    assert first.active_events[-1].uuid == "replaced"
    assert not {"u64", "a64"} & {e.uuid for e in first.active_events}
    monkeypatch.setattr(store_module, "read_window", forbidden)
    assert store.history_window("one", turn_limit=10).active_events == first.active_events


def test_replaced_journal_cannot_reuse_old_display_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = history(tmp_path, turns=150)
    store.discard_business_index("one")
    page = store.history_window("one", turn_limit=10)
    while page.history_cursor:
        page = store.history_window(
            "one", turn_limit=30, before=page.history_cursor, history_epoch=page.history_epoch
        )
    # Replace this test journal with a shorter source. Some old rows deliberately
    # survive the bounded cache cleanup and must remain invisible to all reads.
    path = store._path("one")
    head = path.read_bytes().splitlines()[0]
    item = event("user_message", "replacement", content="only new turn")
    path.write_bytes(head + b"\n" + item.to_jsonl().encode() + b"\n")
    first = store.history_window("one", turn_limit=10)
    monkeypatch.setattr(store_module, "read_window", forbidden)
    cached = store.history_window("one", turn_limit=10)
    assert cached.active_events == first.active_events
    assert [e.uuid for e in cached.active_events] == ["one", "replacement"]
    assert cached.history_cursor is None


def test_preparation_cancel_releases_lease_without_waiting_for_reader(tmp_path: Path) -> None:
    store = history(tmp_path)
    entered, finish = Signal(), Signal()

    def load(sid: str, check: object) -> Session:
        entered.set()
        assert finish.wait(3)
        return store.load(sid)

    preparation = ExecutionPreparation(load, store.acquire_writer_lease, store.can_reuse_loaded)
    try:
        assert preparation.request("one") == "preparing"
        assert entered.wait(2)
        preparation.cancel("one")
        lease = store.acquire_writer_lease("one")
        lease.release()
    finally:
        finish.set()
        preparation.close()
    assert preparation.status("one") == "idle"


def test_preparation_coalesces_pending_switches(tmp_path: Path) -> None:
    store = history(tmp_path)
    entered, finish, latest = Signal(), Signal(), Signal()
    calls: list[str] = []

    def load(sid: str, check: object) -> Session:
        calls.append(sid)
        if sid == "one":
            entered.set()
            assert finish.wait(3)
        else:
            latest.set()
        return store.load("one")

    preparation = ExecutionPreparation(
        load, lambda sid: store.acquire_writer_lease("one"), lambda session: True
    )
    try:
        preparation.request("one")
        assert entered.wait(2)
        for i in range(100):
            preparation.request(f"pending-{i}")
        finish.set()
        assert latest.wait(2)
    finally:
        finish.set()
        preparation.close()
    assert calls == ["one", "pending-99"]
