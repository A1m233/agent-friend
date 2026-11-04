from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from agent.notifications import (
    NotificationConflict,
    NotificationFact,
    NotificationRecord,
    NotificationStore,
)
from memory.host_episodes import presentation

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def test_readers_do_not_reserve_writer_and_writer_can_commit_during_read(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="ready")
    with store._transaction(write=False, operation="test_snapshot") as reader:
        assert reader.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(store.get, item.notification_id).result(timeout=2) == item
            added = pool.submit(store.accept, fact("second"), provided_text="second").result(
                timeout=2
            )
        # A reader sees one stable snapshot while another connection commits.
        assert reader.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM notifications")
    assert store.get(added.notification_id) == added
    assert len(store.list()) == 2


def test_read_does_not_wait_for_pending_writer(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="ready")
    with store._transaction() as writer:
        writer.execute("UPDATE notifications SET text='uncommitted'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(store.get, item.notification_id).result(timeout=2).text == "ready"
            assert pool.submit(store.list).result(timeout=2)[0].text == "ready"
    assert store.get(item.notification_id).text == "uncommitted"


def fact(event_key: str = "run:s:r", *, owner_user_id: str = "local") -> NotificationFact:
    return NotificationFact(
        event_key=event_key,
        kind="reply_ready",
        title="整理照片",
        persona_id="persona",
        session_id="s",
        run_id="r",
        created_at=NOW,
        owner_user_id=owner_user_id,
    )


def present(store: NotificationStore, item: NotificationRecord) -> NotificationRecord:
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    return store.presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=item.text,
        truncated=False,
        now=NOW,
    )


def test_accept_claim_restart_does_not_generate_twice(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact())
    assert store.accept(fact()).notification_id == item.notification_id
    assert store.claim_next() is not None
    assert store.claim_next() is None
    recovered = NotificationStore(store.path).recover_generation()
    assert len(recovered) == 1
    assert recovered[0].text_source == "fallback"
    assert store.claim_next() is None
    assert store.recover_generation() == []


def test_presentation_claim_reload_and_receipt_race(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="照片有新回复，回来看看吧。")
    first = store.prepare(item.notification_id, version=item.version, window_id="window-1")
    second = store.prepare(item.notification_id, version=first.version, window_id="window-2")
    assert second.delivery["attempt"] == 2
    with pytest.raises(NotificationConflict):
        store.presented(
            item.notification_id,
            attempt=1,
            text=item.text,
            truncated=False,
            now=NOW,
        )

    def receipt(_: int) -> NotificationRecord:
        return store.presented(
            item.notification_id,
            attempt=2,
            text=item.text,
            truncated=False,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(executor.map(receipt, range(4)))
        assert all(item == receipts[0] for item in receipts)
    shown = store.get(item.notification_id)
    assert shown.delivery["memorySummary"]
    assert store.suppress(item.notification_id).delivery["status"] == "presented"
    closed = store.dismiss(item.notification_id, version=shown.version, now=NOW)
    assert closed.delivery["presentedText"] == item.text
    assert store.list()[0].delivery["status"] == "dismissed"


def test_receipt_cannot_add_facts_or_replace_first_text(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="这是一段较长的提醒。")
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    with pytest.raises(NotificationConflict):
        store.presented(
            item.notification_id,
            attempt=prepared.delivery["attempt"],
            text="任务已成功",
            truncated=False,
            now=NOW,
        )

    shown = store.presented(
        item.notification_id,
        attempt=1,
        text="这是一段…",
        truncated=True,
        now=NOW,
    )
    assert "截断" in shown.delivery["memorySummary"]
    with pytest.raises(NotificationConflict):
        store.presented(
            item.notification_id,
            attempt=1,
            text=item.text,
            truncated=False,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("offset_minutes", "expected_time"),
    [(480, "2026-09-06 08:00:00 +08:00"), (-210, "2026-09-05 20:30:00 -03:30")],
)
def test_receipt_summary_uses_local_time_without_changing_utc_identity_or_first_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offset_minutes: int, expected_time: str
) -> None:
    zone = timezone(timedelta(minutes=offset_minutes))
    monkeypatch.setattr(presentation, "_local_time", lambda value: value.astimezone(zone))
    store = NotificationStore(tmp_path / "notifications.db")
    text = '原文里保留 2026-09-06T00:00:00+00:00 和 "引号"。'
    item = store.accept(fact(), provided_text=text)
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    when = NOW.replace(microsecond=123456)
    shown = store.presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=text,
        truncated=False,
        now=when,
    )
    assert shown.delivery["presentedAt"] == "2026-09-06T00:00:00.123456+00:00"
    assert shown.delivery["memorySummary"] == (
        f"{expected_time}，我通过桌宠气泡就「整理照片」发出提醒，实际显示："
        + json.dumps(text, ensure_ascii=False)
        + "。"
    )
    assert shown.delivery["presentedText"] == text
    assert shown.delivery["memoryStatus"] == "pending"
    assert shown.delivery["soundHandled"] is False

    monkeypatch.setattr(presentation, "_local_time", lambda value: value.astimezone(UTC))
    repeated = NotificationStore(store.path).presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=text,
        truncated=False,
        now=when + timedelta(hours=1),
    )
    assert repeated == shown


def test_dismiss_before_receipt_preserves_closed_state_but_records_fact(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="回复到了。")
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    store.dismiss(item.notification_id, version=prepared.version, now=NOW)
    closed = store.presented(
        item.notification_id,
        attempt=1,
        text=item.text,
        truncated=False,
        now=NOW,
    )
    assert closed.delivery["status"] == "dismissed"
    assert closed.delivery["presentedAt"] is not None
    assert closed.delivery["memorySummary"] is not None


def test_snapshot_does_not_lose_old_live_or_unprojected_notifications(tmp_path: Path) -> None:
    from dataclasses import replace

    store = NotificationStore(tmp_path / "notifications.db")
    for index in range(505):
        store.accept(replace(fact(), event_key=f"run:s:{index}"), provided_text="新回复。")
    assert len(store.list()) == 505


def test_sound_claim_is_atomic_durable_and_does_not_acknowledge_presentation(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="ready")
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: store.consume_sound(item.notification_id), range(8)))
    assert sum(claims) == 1
    restored = NotificationStore(store.path)
    assert not restored.consume_sound(item.notification_id)
    after = restored.get(item.notification_id)
    assert after.version == item.version
    assert after.to_wire() == item.to_wire()
    assert after.delivery["presentedAt"] is None
    assert after.delivery["memorySummary"] is None
    shown = present(restored, after)
    assert shown.delivery["presentedAt"] is not None
    assert shown.delivery["memorySummary"]
    assert not restored.consume_sound(item.notification_id)


@pytest.mark.parametrize("status", ["generating", "dismissed", "invalidated"])
def test_sound_claim_rejects_ineligible_notifications(tmp_path: Path, status: str) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text=None if status == "generating" else "ready")
    if status == "generating":
        store.claim_next()
    elif status == "dismissed":
        store.dismiss(item.notification_id, version=item.version, now=NOW)
    elif status == "invalidated":
        store.suppress(item.notification_id)
    assert not store.consume_sound(item.notification_id)


def test_presentation_winning_the_race_does_not_consume_arrival_sound(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="ready")
    shown = present(store, item)
    assert shown.delivery["soundHandled"] is False
    assert store.consume_sound(item.notification_id)
    assert not store.consume_sound(item.notification_id)


def test_closing_a_row_does_not_consume_another_arrival(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="close")
    waiting = store.accept(fact("waiting"), provided_text="waiting")
    store.dismiss(closed.notification_id, version=closed.version, now=NOW)
    assert store.get(waiting.notification_id) == waiting
    assert store.consume_sound(waiting.notification_id)
