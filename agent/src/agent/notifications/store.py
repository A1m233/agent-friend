"""SQLite ledger: first presentation and its memory projection are separate facts."""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory.host_episodes.presentation import format_notification_time

from .models import NotificationFact, NotificationRecord, fallback_text


class NotificationConflict(ValueError):
    pass


class NotificationNotFound(LookupError):
    pass


class NotificationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    facts_json TEXT NOT NULL, generation_status TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '', text_source TEXT, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, diagnostic TEXT
                );
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    notification_id TEXT NOT NULL REFERENCES notifications(notification_id),
                    channel TEXT NOT NULL, state_json TEXT NOT NULL,
                    PRIMARY KEY(notification_id, channel)
                );
            """)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _record(conn: sqlite3.Connection, notification_id: str) -> NotificationRecord:
        row = conn.execute(
            "SELECT * FROM notifications WHERE notification_id=?", (notification_id,)
        ).fetchone()
        if row is None:
            raise NotificationNotFound(notification_id)
        delivery = conn.execute(
            "SELECT state_json FROM notification_deliveries WHERE notification_id=? AND channel='pet'",
            (notification_id,),
        ).fetchone()
        return NotificationRecord(
            NotificationFact.from_dict(json.loads(row["facts_json"])),
            row["generation_status"],
            row["text"],
            row["text_source"],
            row["version"],
            json.loads(delivery[0]),
        )

    def get(self, notification_id: str) -> NotificationRecord:
        with self._transaction() as conn:
            return self._record(conn, notification_id)

    def list(self, *, limit: int | None = None) -> list[NotificationRecord]:
        with self._transaction() as conn:
            query = "SELECT notification_id FROM notifications ORDER BY created_at DESC"
            ids = (
                conn.execute(query + " LIMIT ?", (limit,)).fetchall()
                if limit is not None
                else conn.execute(query).fetchall()
            )
            return [self._record(conn, row[0]) for row in ids]

    def accept(
        self, fact: NotificationFact, *, provided_text: str | None = None
    ) -> NotificationRecord:
        payload = fact.to_dict()
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notifications VALUES(?,?,?,?,?,?,1,?,?,NULL)",
                (
                    fact.notification_id,
                    f"{fact.owner_user_id}:{fact.event_key}",
                    json.dumps(payload, ensure_ascii=False),
                    "ready" if provided_text else "pending",
                    provided_text or "",
                    "provided" if provided_text else None,
                    payload["created_at"],
                    payload["expires_at"],
                ),
            )
            state = {
                "channel": "pet",
                "status": "pending",
                "attempt": 0,
                "windowId": None,
                "presentedAt": None,
                "presentedText": None,
                "truncated": False,
                "dismissedAt": None,
                "soundHandled": False,
                "memoryStatus": "pending",
                "memorySummary": None,
            }
            conn.execute(
                "INSERT OR IGNORE INTO notification_deliveries VALUES(?, 'pet', ?)",
                (fact.notification_id, json.dumps(state)),
            )
            return self._record(conn, fact.notification_id)

    def recover_generation(self) -> builtins.list[NotificationRecord]:
        with self._transaction() as conn:
            ids = conn.execute(
                "SELECT notification_id FROM notifications WHERE generation_status='generating'"
            ).fetchall()
            for row in ids:
                item = self._record(conn, row[0])
                conn.execute(
                    "UPDATE notifications SET generation_status='ready', text=?, text_source='fallback', "
                    "version=version+1, diagnostic='generation_interrupted' WHERE notification_id=?",
                    (fallback_text(item.fact), row[0]),
                )
            return [self._record(conn, row[0]) for row in ids]

    def claim_next(self) -> NotificationRecord | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT notification_id FROM notifications WHERE generation_status='pending' "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE notifications SET generation_status='generating', version=version+1 WHERE notification_id=?",
                (row[0],),
            )
            return self._record(conn, row[0])

    def ready(
        self, notification_id: str, text: str, *, source: str, diagnostic: str = ""
    ) -> NotificationRecord:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE notifications SET generation_status='ready', text=?, text_source=?, "
                "diagnostic=?, version=version+1 WHERE notification_id=? AND generation_status='generating'",
                (text, source, diagnostic, notification_id),
            )
            return self._record(conn, notification_id)

    @staticmethod
    def _save_delivery(
        conn: sqlite3.Connection, notification_id: str, state: dict[str, Any]
    ) -> None:
        conn.execute(
            "UPDATE notification_deliveries SET state_json=? WHERE notification_id=? AND channel='pet'",
            (json.dumps(state, ensure_ascii=False), notification_id),
        )
        conn.execute(
            "UPDATE notifications SET version=version+1 WHERE notification_id=?", (notification_id,)
        )

    def suppress(self, notification_id: str, *, expired: bool = False) -> NotificationRecord:
        with self._transaction() as conn:
            item = self._record(conn, notification_id)
            if item.delivery["status"] in {"presented", "dismissed", "invalidated"}:
                return item
            state = dict(item.delivery, status="invalidated")
            conn.execute(
                "UPDATE notifications SET generation_status=? WHERE notification_id=?",
                ("expired" if expired else "suppressed", notification_id),
            )
            self._save_delivery(conn, notification_id, state)
            return self._record(conn, notification_id)

    def prepare(self, notification_id: str, *, version: int, window_id: str) -> NotificationRecord:
        with self._transaction() as conn:
            item = self._record(conn, notification_id)
            state = item.delivery
            if item.generation_status != "ready" or state["status"] in {"dismissed", "invalidated"}:
                raise NotificationConflict("notification is no longer available")
            if state["status"] == "presented":
                return item
            if state["status"] == "prepared" and state["windowId"] == window_id:
                return item
            if item.version != version:
                raise NotificationConflict("notification version changed")
            state.update(status="prepared", attempt=state["attempt"] + 1, windowId=window_id)
            self._save_delivery(conn, notification_id, state)
            return self._record(conn, notification_id)

    def presented(
        self,
        notification_id: str,
        *,
        attempt: int,
        text: str,
        truncated: bool,
        now: datetime,
        sound_enabled: bool,
    ) -> tuple[NotificationRecord, bool]:
        with self._transaction() as conn:
            item = self._record(conn, notification_id)
            state = item.delivery
            if state["attempt"] != attempt or state["status"] not in {
                "prepared",
                "presented",
                "dismissed",
            }:
                raise NotificationConflict("stale presentation attempt")
            if state["presentedAt"] is not None:
                if state["presentedText"] != text or state["truncated"] != truncated:
                    raise NotificationConflict("presentation receipt is immutable")
                return item, False
            prefix = text.removesuffix("…").removesuffix("...") if truncated else text
            if (
                not text
                or len(text) > 160
                or (truncated and (not prefix or not item.text.startswith(prefix)))
                or (not truncated and text != item.text)
            ):
                raise NotificationConflict("receipt must describe the provided text")
            timestamp = now.astimezone(UTC).isoformat()
            subject = item.fact.title or "对话"
            summary = (
                f"{format_notification_time(now)}，我通过桌宠气泡就「{subject}」发出提醒，实际显示："
                f"{json.dumps(text, ensure_ascii=False)}。"
                + ("内容在显示时被截断。" if truncated else "")
            )
            already_dismissed = state["status"] == "dismissed"
            sound_already_handled = state.get("soundHandled") is True
            state.update(
                status="dismissed" if already_dismissed else "presented",
                presentedAt=timestamp,
                presentedText=text,
                truncated=truncated,
                soundHandled=True,
                memorySummary=summary,
            )
            self._save_delivery(conn, notification_id, state)
            return self._record(conn, notification_id), (
                sound_enabled and not already_dismissed and not sound_already_handled
            )

    def dismiss(
        self,
        notification_id: str,
        *,
        version: int,
        now: datetime,
        silent_replacement_ids: Sequence[str] = (),
    ) -> NotificationRecord:
        with self._transaction() as conn:
            item = self._record(conn, notification_id)
            if item.delivery["status"] == "invalidated":
                return item
            if item.delivery["status"] != "dismissed":
                if item.version != version:
                    raise NotificationConflict("notification version changed")
                state = dict(
                    item.delivery, status="dismissed", dismissedAt=now.astimezone(UTC).isoformat()
                )
                self._save_delivery(conn, notification_id, state)
            for replacement_id in dict.fromkeys(silent_replacement_ids):
                if replacement_id == notification_id:
                    continue
                try:
                    replacement = self._record(conn, replacement_id)
                except NotificationNotFound:
                    continue
                if (
                    replacement.fact.owner_user_id != item.fact.owner_user_id
                    or replacement.delivery["status"] not in {"pending", "prepared"}
                    or replacement.delivery.get("soundHandled") is True
                ):
                    continue
                replacement_state = dict(replacement.delivery, soundHandled=True)
                # Sound eligibility is internal and does not invalidate a prepared claim.
                conn.execute(
                    "UPDATE notification_deliveries SET state_json=? "
                    "WHERE notification_id=? AND channel='pet'",
                    (json.dumps(replacement_state, ensure_ascii=False), replacement_id),
                )
            return self._record(conn, notification_id)

    def mark_memory(self, notification_id: str, status: str) -> None:
        with self._transaction() as conn:
            item = self._record(conn, notification_id)
            state = dict(item.delivery, memoryStatus=status)
            # Projection status does not change the public presentation revision.
            conn.execute(
                "UPDATE notification_deliveries SET state_json=? WHERE notification_id=? AND channel='pet'",
                (json.dumps(state, ensure_ascii=False), notification_id),
            )
