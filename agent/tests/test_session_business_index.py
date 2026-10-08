from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event as Signal
from types import SimpleNamespace

import pytest
from agent.sessions import rollout_encoding
from agent.sessions.events import EventType

from agent import Event, JsonlSessionStore, Session, SessionCorruptError


def event(event_type: EventType, uuid: str, **payload: object) -> Event:
    return Event(
        type=event_type, uuid=uuid, ts=datetime.now(UTC), payload=payload, meta={"run_id": "run"}
    )


def make_store(root: Path) -> JsonlSessionStore:
    store = JsonlSessionStore(root)
    store.create(Session.new("History", "default", "test/model", session_id="one"))
    return store


def forbid(*args: object, **kwargs: object) -> None:
    raise AssertionError("existing history must not be scanned")


def test_persistent_projection_survives_new_store_and_memory_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.append_events(
        "one",
        [
            event("user_message", "user", content="hello"),
            event("assistant_message", "answer", content="world"),
        ],
    )
    original = store.query("one")
    fresh = JsonlSessionStore(tmp_path, query_cache_entries=0, query_cache_bytes=0)
    monkeypatch.setattr(fresh, "_read_records", forbid)
    assert fresh.query("one").active_events == original.active_events
    assert fresh.load_business("one").active_events == list(original.active_events)
    # Page reads do not hydrate the full business projection either.
    monkeypatch.setattr(fresh, "query", forbid)
    assert fresh.history_page("one")["events"][-1]["uuid"] == "answer"


def test_large_tool_body_is_deferred_and_exactly_retrievable(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content = "汉字😀\n" * 20000
    store.append_events(
        "one",
        [
            event("user_message", "user", content="do it"),
            event(
                "tool_call_request",
                "call",
                event_tool_call_id="tool1",
                tool_name="exec_command",
                arguments_json='{"cmd":"ok"}',
            ),
            event(
                "tool_call_result",
                "result",
                event_tool_call_id="tool1",
                content=content,
                is_error=False,
            ),
            event("assistant_message", "answer", content="done"),
            event("run_terminal", "terminal", run_id="run", status="completed"),
        ],
    )
    page = store.history_page("one", item_limit=2, byte_limit=8192)
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 8192
    assert [item["uuid"] for item in page["events"]] == ["call", "answer"]
    tool = page["events"][0]
    assert tool["message_start"] and page["events"][-1]["message_end"]
    assert tool["result_event"]["body"]["state"] == "deferred"
    assert tool["run"]["status"] == "completed"
    text, offset = "", 0
    while True:
        chunk = store.history_content(
            "one", "result", version=page["content_version"], offset=offset, limit=65536
        )
        assert len(chunk["text"].encode()) <= 65536
        text += chunk["text"]
        offset = chunk["next_offset"]
        if chunk["complete"]:
            break
    assert json.loads(text)["payload"]["content"] == content
    around = store.history_page("one", around="result", item_limit=1)
    assert around["events"][0]["uuid"] == "call"
    copy_page = store.history_page("one", message_id=tool["message_id"])
    assert [item["uuid"] for item in copy_page["events"]] == ["call", "answer"]


def test_known_appends_and_rewrite_preserve_navigation_or_reject_stale_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "old", content="old"))
    first = store.history_page("one")
    monkeypatch.setattr(store, "_read_records", forbid)
    store.append_events(
        "one", [event("rollout_commit", "internal"), event("assistant_message", "a", content="yes")]
    )
    second = store.history_page("one", epoch=first["history_epoch"])
    assert first["content_version"] == second["content_version"]
    body = store.history_content("one", "old", version=first["content_version"])
    assert json.loads(body["text"])["payload"]["content"] == "old"
    store.append_event("one", event("turn_rewrite", "rewrite", inactive_event_uuids=["old", "a"]))
    with pytest.raises(ValueError, match="changed"):
        store.history_page("one", epoch=first["history_epoch"])
    with pytest.raises(ValueError, match="changed"):
        store.history_content("one", "old", version=first["content_version"])
    assert not store.history_page("one")["events"]


def test_deferred_chunks_remain_consistent_across_known_appends(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content = "汉字😀" * 10000
    store.append_event("one", event("assistant_message", "large", content=content))
    page = store.history_page("one")
    first = store.history_content("one", "large", version=page["content_version"], limit=4096)
    store.append_event("one", event("assistant_message", "later", content="next"))
    chunks = [first["text"]]
    offset = first["next_offset"]
    while True:
        chunk = store.history_content(
            "one", "large", version=page["content_version"], offset=offset, limit=4096
        )
        chunks.append(chunk["text"])
        offset = chunk["next_offset"]
        if chunk["complete"]:
            break
    assert json.loads("".join(chunks))["payload"]["content"] == content


def test_deferred_run_error_preserves_production_recovery_fields(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    message = "上游错误😀: provider failed\n" * 2000
    store.append_events(
        "one",
        [
            event("user_message", "user", content="continue"),
            event(
                "run_error",
                "failure",
                run_id="run",
                code="provider_error",
                message=message,
                reason="provider_unavailable",
                recovery_action="retry",
                recoverable=True,
            ),
        ],
    )
    page = store.history_page("one", byte_limit=8192)
    failure = page["events"][-1]
    assert failure["body"]["state"] == "deferred"
    assert failure["run"]["error_code"] == "provider_error"
    assert failure["run"]["error_reason"] == "provider_unavailable"
    assert failure["run"]["recovery_action"] == "retry"
    assert failure["run"]["can_continue"]
    assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= 8192
    assert failure["run"]["error_truncated"]
    assert failure["run"]["error_event_uuid"] == "failure"
    assert len(failure["run"]["error"].encode("utf-8")) <= 1024
    text, offset = "", 0
    while True:
        chunk = store.history_content(
            "one", "failure", version=page["content_version"], offset=offset, limit=4096
        )
        text += chunk["text"]
        offset = chunk["next_offset"]
        if chunk["complete"]:
            break
    assert json.loads(text)["payload"]["message"] == message


def test_damaged_database_rebuilds_and_source_corruption_remains_an_error(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="ok"))
    expected = store.history_page("one")["events"]
    store._business_index.path("one").write_bytes(b"not sqlite")
    fresh = JsonlSessionStore(tmp_path)
    assert fresh.history_page("one")["events"] == expected
    path = tmp_path / "one.jsonl"
    path.write_bytes(path.read_bytes() + b"not JSON\n")
    with pytest.raises(SessionCorruptError):
        JsonlSessionStore(tmp_path).history_page("one")


def test_business_decode_validates_references_without_copying_model_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    encoder = rollout_encoding.RolloutEncoder()
    encoded = encoder.encode(
        event(
            "rollout_request_prepared",
            "prepared",
            model_context={"large": "x" * 100000},
            epoch_manifest={"envelope": {"a": "b"}},
        )
    )
    store.append_events("one", encoded)
    monkeypatch.setattr(rollout_encoding, "copy", SimpleNamespace(deepcopy=forbid))
    assert store.history_page("one")["events"] == []
    store.discard_business_index("one")
    # The same optimized reader still checks the internal value's digest.
    store.append_event(
        "one",
        event(
            "rollout_value",
            "bad",
            encoding_version=1,
            kind="model_context",
            sha256="wrong",
            value={},
        ),
    )
    with pytest.raises(SessionCorruptError, match="digest"):
        store.history_page("one")


def test_append_during_initial_build_publishes_caught_up_index_without_rescan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    entered, release = Signal(), Signal()
    original = store._read_records
    calls = 0

    def scan(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        calls += 1
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(store, "_read_records", scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(store.history_page, "one")
        assert entered.wait(5)
        try:
            store.append_event("one", event("user_message", "added", content="new"))
        finally:
            release.set()
        assert future.result(5)["events"][-1]["uuid"] == "added"
    assert calls == 1
    monkeypatch.setattr(store, "_read_records", forbid)
    assert store.history_page("one")["events"][-1]["uuid"] == "added"


def test_delete_removes_derived_bodies(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.history_page("one")
    path = store._business_index.path("one")
    assert path.exists()
    store.delete("one")
    assert not path.exists()


def test_empty_run_header_follows_user_and_disappears_when_body_arrives(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_events(
        "one",
        [
            event("run_started", "started", run_id="run", started_at="2026-01-01T00:00:00Z"),
            event("user_message", "user", content="hello"),
            event("run_terminal", "terminal", run_id="run", status="interrupted"),
        ],
    )
    page = store.history_page("one")
    assert [item["uuid"] for item in page["events"]] == ["user", "started"]
    assert page["events"][-1]["message_id"] == "run:run"
    assert page["events"][-1]["run"]["can_continue"]
    store.append_event("one", event("assistant_message", "answer", content="partial"))
    assert [item["uuid"] for item in store.history_page("one")["events"]] == ["user", "answer"]


def test_index_maintenance_failure_does_not_fail_durable_append_and_can_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.history_page("one")
    with monkeypatch.context() as patch:
        patch.setattr(
            store._business_index,
            "advance",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        store.append_event("one", event("user_message", "saved", content="once"))
    # A valid warm in-memory snapshot repairs a missed disk-index update.
    assert store.history_page("one")["events"][-1]["uuid"] == "saved"
    assert sum(e.uuid == "saved" for e in store.load("one").events) == 1


def test_cancelled_build_is_not_published_and_other_sessions_remain_available(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_events("one", [event("user_message", f"u{i}", content="hi") for i in range(20)])
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls >= 15:
            raise InterruptedError("navigation left")

    with pytest.raises(InterruptedError, match="navigation left"):
        store.history_page("one", cancel_check=cancel)
    assert not store._business_index.matches("one", store._source_version(tmp_path / "one.jsonl"))
    assert len(store.history_page("one")["events"]) == 20


def test_around_large_anchor_is_included_under_byte_bound(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_events(
        "one", [event("user_message", f"u{i}", content="x" * 16000) for i in range(20)]
    )
    page = store.history_page("one", around="u10", byte_limit=8192)
    assert page["events"][0]["uuid"] == "u10"
    assert page["has_before"] and len(json.dumps(page).encode()) < 8192


def test_new_process_reads_persistent_page_without_raw_or_full_business_loading(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_event("one", event("user_message", "u", content="hello"))
    store.history_page("one")
    program = """
import sys
from agent import JsonlSessionStore
store = JsonlSessionStore(sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError('persistent page must not scan or hydrate full history')
store._read_records = forbidden
store.query = forbidden
assert store.history_page('one')['events'][0]['uuid'] == 'u'
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_one_shot_io_shaped_cancel_is_not_reclassified_as_cache_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store.history_page("one")
    fresh = JsonlSessionStore(tmp_path)
    original = fresh._business_index.read
    reached = False
    cancelled = False

    def entering(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal reached
        reached = True
        return original(*args, **kwargs)  # type: ignore[arg-type]

    def cancel_once() -> None:
        nonlocal cancelled
        if reached and not cancelled:
            cancelled = True
            raise InterruptedError("one cancellation")

    monkeypatch.setattr(fresh._business_index, "read", entering)
    with pytest.raises(InterruptedError, match="one cancellation"):
        fresh.query("one", cancel_check=cancel_once)
    assert fresh.history_page("one")["events"] == []
