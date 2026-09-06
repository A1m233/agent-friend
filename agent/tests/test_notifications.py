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
    NotificationNotFound,
    NotificationRecord,
    NotificationStore,
)
from memory.host_episodes import presentation

NOW = datetime(2026, 9, 6, tzinfo=UTC)


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


def present(store: NotificationStore, item: NotificationRecord) -> tuple[NotificationRecord, bool]:
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    return store.presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=item.text,
        truncated=False,
        now=NOW,
        sound_enabled=True,
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
            sound_enabled=True,
        )

    def receipt(_: int) -> bool:
        return store.presented(
            item.notification_id,
            attempt=2,
            text=item.text,
            truncated=False,
            now=NOW,
            sound_enabled=True,
        )[1]

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(receipt, range(4))) == 1
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
            sound_enabled=True,
        )

    shown, sound = store.presented(
        item.notification_id,
        attempt=1,
        text="这是一段…",
        truncated=True,
        now=NOW,
        sound_enabled=False,
    )
    assert not sound
    assert "截断" in shown.delivery["memorySummary"]
    with pytest.raises(NotificationConflict):
        store.presented(
            item.notification_id,
            attempt=1,
            text=item.text,
            truncated=False,
            now=NOW,
            sound_enabled=True,
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
    shown, sound = store.presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=text,
        truncated=False,
        now=when,
        sound_enabled=True,
    )
    assert sound
    assert shown.delivery["presentedAt"] == "2026-09-06T00:00:00.123456+00:00"
    assert shown.delivery["memorySummary"] == (
        f"{expected_time}，我通过桌宠气泡就「整理照片」发出提醒，实际显示："
        + json.dumps(text, ensure_ascii=False)
        + "。"
    )
    assert shown.delivery["presentedText"] == text
    assert shown.delivery["memoryStatus"] == "pending"
    assert shown.delivery["soundHandled"] is True

    monkeypatch.setattr(presentation, "_local_time", lambda value: value.astimezone(UTC))
    repeated, second_sound = NotificationStore(store.path).presented(
        item.notification_id,
        attempt=prepared.delivery["attempt"],
        text=text,
        truncated=False,
        now=when + timedelta(hours=1),
        sound_enabled=True,
    )
    assert repeated == shown
    assert not second_sound


def test_dismiss_before_receipt_preserves_closed_state_but_records_fact(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="回复到了。")
    prepared = store.prepare(item.notification_id, version=item.version, window_id="bubble")
    store.dismiss(item.notification_id, version=prepared.version, now=NOW)
    closed, sound = store.presented(
        item.notification_id,
        attempt=1,
        text=item.text,
        truncated=False,
        now=NOW,
        sound_enabled=True,
    )
    assert closed.delivery["status"] == "dismissed"
    assert closed.delivery["presentedAt"] is not None
    assert closed.delivery["memorySummary"] is not None
    assert not sound


def test_snapshot_does_not_lose_old_live_or_unprojected_notifications(tmp_path: Path) -> None:
    from dataclasses import replace

    store = NotificationStore(tmp_path / "notifications.db")
    for index in range(505):
        store.accept(replace(fact(), event_key=f"run:s:{index}"), provided_text="新回复。")
    assert len(store.list()) == 505


def test_dismiss_silences_only_named_pending_and_prepared_replacements(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    pending = store.accept(fact("pending"), provided_text="等待补位。")
    claimed = store.accept(fact("prepared"), provided_text="已领取的补位。")
    prepared = store.prepare(claimed.notification_id, version=claimed.version, window_id="bubble")
    unrelated = store.accept(fact("unrelated"), provided_text="名单外的通知。")

    dismissed = store.dismiss(
        closed.notification_id,
        version=closed.version,
        now=NOW,
        silent_replacement_ids=[pending.notification_id, prepared.notification_id],
    )
    assert dismissed.delivery["status"] == "dismissed"
    assert dismissed.version == closed.version + 1
    for before in (pending, prepared):
        after = store.get(before.notification_id)
        assert after.version == before.version
        assert after.to_wire() == before.to_wire()
        assert after.delivery == {**before.delivery, "soundHandled": True}
        shown, sound = present(store, after)
        assert not sound
        assert shown.delivery["presentedAt"] is not None
        assert shown.delivery["presentedText"] == before.text
        assert shown.delivery["memorySummary"]
        assert shown.delivery["memoryStatus"] == "pending"

    assert present(store, unrelated)[1]
    newly_arrived = store.accept(fact("new-arrival"), provided_text="稍后新来的通知。")
    assert present(store, newly_arrived)[1]


def test_dismiss_without_explicit_replacements_keeps_waiting_notification_audible(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    waiting = store.accept(fact("waiting"), provided_text="正常的新通知。")
    store.dismiss(closed.notification_id, version=closed.version, now=NOW)
    assert store.get(waiting.notification_id) == waiting
    assert present(store, waiting)[1]


@pytest.mark.parametrize("failure", ["stale-version", "missing-original", "invalidated-original"])
def test_failed_or_unavailable_dismiss_does_not_silence_replacements(
    tmp_path: Path, failure: str
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    waiting = store.accept(fact("waiting"), provided_text="仍应正常响铃。")
    ids = [waiting.notification_id]
    if failure == "stale-version":
        store.prepare(closed.notification_id, version=closed.version, window_id="bubble")
        with pytest.raises(NotificationConflict):
            store.dismiss(
                closed.notification_id, version=closed.version, now=NOW, silent_replacement_ids=ids
            )
    elif failure == "missing-original":
        with pytest.raises(NotificationNotFound):
            store.dismiss("missing", version=1, now=NOW, silent_replacement_ids=ids)
    else:
        invalidated = store.suppress(closed.notification_id)
        assert (
            store.dismiss(
                closed.notification_id, version=closed.version, now=NOW, silent_replacement_ids=ids
            )
            == invalidated
        )
    assert store.get(waiting.notification_id) == waiting
    assert present(store, waiting)[1]


def test_dismiss_and_all_replacement_sound_updates_roll_back_together(tmp_path: Path) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    first = store.accept(fact("first"), provided_text="第一条补位。")
    second = store.accept(fact("second"), provided_text="第二条补位。")
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_second_replacement BEFORE UPDATE ON notification_deliveries "
            f"WHEN NEW.notification_id = '{second.notification_id}' "
            "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated write failure"):
        store.dismiss(
            closed.notification_id,
            version=closed.version,
            now=NOW,
            silent_replacement_ids=[first.notification_id, second.notification_id],
        )
    assert store.get(closed.notification_id) == closed
    assert store.get(first.notification_id) == first
    assert store.get(second.notification_id) == second


def test_silent_replacement_survives_idempotent_dismiss_restart_and_window_reclaim(
    tmp_path: Path,
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    waiting = store.accept(fact("waiting"), provided_text="重领后仍静默。")
    prepared = store.prepare(waiting.notification_id, version=waiting.version, window_id="old")
    ids = [waiting.notification_id, waiting.notification_id]
    dismissed = store.dismiss(
        closed.notification_id, version=closed.version, now=NOW, silent_replacement_ids=ids
    )
    store = NotificationStore(store.path)
    assert (
        store.dismiss(
            closed.notification_id,
            version=closed.version,
            now=NOW + timedelta(seconds=1),
            silent_replacement_ids=ids,
        )
        == dismissed
    )
    reclaimed = store.prepare(
        waiting.notification_id, version=prepared.version, window_id="replacement-window"
    )
    assert reclaimed.delivery["attempt"] == prepared.delivery["attempt"] + 1
    assert reclaimed.delivery["soundHandled"] is True
    shown, sound = store.presented(
        waiting.notification_id,
        attempt=reclaimed.delivery["attempt"],
        text=waiting.text,
        truncated=False,
        now=NOW,
        sound_enabled=True,
    )
    assert not sound
    assert shown.delivery["memorySummary"]
    store = NotificationStore(store.path)
    assert store.presented(
        waiting.notification_id,
        attempt=reclaimed.delivery["attempt"],
        text=waiting.text,
        truncated=False,
        now=NOW,
        sound_enabled=True,
    ) == (shown, False)
    assert (
        store.prepare(waiting.notification_id, version=shown.version, window_id="third-window")
        == shown
    )
    assert (
        store.dismiss(
            closed.notification_id, version=closed.version, now=NOW, silent_replacement_ids=ids
        )
        == dismissed
    )
    assert store.get(waiting.notification_id) == shown


@pytest.mark.parametrize("target_status", ["cross-owner", "presented", "dismissed", "invalidated"])
def test_silent_replacement_list_skips_other_owners_and_terminal_deliveries(
    tmp_path: Path, target_status: str
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    closed = store.accept(fact("closed"), provided_text="关闭这条。")
    target = store.accept(
        fact("target", owner_user_id="other" if target_status == "cross-owner" else "local"),
        provided_text="不应改动这条记录。",
    )
    if target_status == "presented":
        target, _sound = present(store, target)
    elif target_status == "dismissed":
        target = store.dismiss(target.notification_id, version=target.version, now=NOW)
    elif target_status == "invalidated":
        target = store.suppress(target.notification_id)
    eligible = store.accept(fact("eligible"), provided_text="只有这条补位静默。")
    result = store.dismiss(
        closed.notification_id,
        version=closed.version,
        now=NOW,
        silent_replacement_ids=[
            target.notification_id,
            "missing",
            closed.notification_id,
            eligible.notification_id,
        ],
    )
    assert store.get(target.notification_id) == target
    assert result.delivery["soundHandled"] is False
    assert store.get(eligible.notification_id).delivery["soundHandled"] is True
    if target_status == "cross-owner":
        assert present(store, target)[1]


@pytest.mark.parametrize("sound_handled", [None, False, True])
def test_first_receipt_reads_existing_sound_eligibility_with_legacy_state_compatibility(
    tmp_path: Path, sound_handled: bool | None
) -> None:
    store = NotificationStore(tmp_path / "notifications.db")
    item = store.accept(fact(), provided_text="兼容已有通知状态。")
    state = dict(item.delivery)
    if sound_handled is None:
        del state["soundHandled"]
    else:
        state["soundHandled"] = sound_handled
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE notification_deliveries SET state_json=? WHERE notification_id=?",
            (json.dumps(state), item.notification_id),
        )
    shown, sound = present(store, store.get(item.notification_id))
    assert sound is (sound_handled is not True)
    assert shown.delivery["soundHandled"] is True
    assert shown.delivery["presentedAt"] is not None
    assert shown.delivery["memorySummary"]
