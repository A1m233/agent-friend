"""Durable prepared-message outbox, independent of model execution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4


class SessionDeliveryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT UNIQUE NOT NULL, request_json TEXT NOT NULL,
                conversation_state TEXT NOT NULL DEFAULT 'queued',
                im_state TEXT NOT NULL, message_id TEXT, provider_receipt TEXT,
                error TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
            db.execute(
                "UPDATE deliveries SET im_state='unknown',error='interrupted_send' WHERE im_state='sending'"
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(
        self, operation_key: str, request: dict[str, Any], *, ttl_seconds: int = 1800
    ) -> dict[str, Any]:
        raw = json.dumps(request, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        now = datetime.now(UTC)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM deliveries WHERE operation_key=? OR fingerprint=?",
                (operation_key, fingerprint),
            ).fetchone()
            if existing:
                saved = json.loads(existing["request_json"])
                # A replay of an automatic delivery keeps the recipient pinned at acceptance.
                if request.get("auto_qq") and saved.get("auto_qq"):
                    saved["destination_id"] = request.get("destination_id")
                if saved.get("first_contact") and request.get("first_contact"):
                    saved["target_session_id"] = request["target_session_id"]
                if saved != request:
                    raise ValueError("delivery_operation_conflict")
                return self._decode(existing)
            delivery_id = str(uuid4())
            db.execute(
                "INSERT INTO deliveries (delivery_id,operation_key,fingerprint,request_json,im_state,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
                (
                    delivery_id,
                    operation_key,
                    fingerprint,
                    raw,
                    "pending" if request.get("destination_id") else "not_requested",
                    now.isoformat(),
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                ),
            )
        return self.get(delivery_id)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        return result

    def for_message(self, session_id: str, message_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT d.* FROM deliveries d WHERE
                (json_extract(d.request_json,'$.target_session_id')=? AND d.message_id=?)
                OR EXISTS (SELECT 1 FROM json_each(d.request_json,'$.source_messages') s
                    WHERE json_extract(s.value,'$.session_id')=? AND json_extract(s.value,'$.message_id')=?)
                ORDER BY d.created_at DESC LIMIT 10""",
                (session_id, message_id, session_id, message_id),
            ).fetchall()
        return [
            {
                "delivery_id": r["delivery_id"],
                "target_session_id": json.loads(r["request_json"])["target_session_id"],
                "message_id": r["message_id"],
                "conversation_state": r["conversation_state"],
                "im_state": r["im_state"],
                "created_at": r["created_at"],
                "im_read_status": "unknown",
            }
            for r in rows
        ]

    def get(self, delivery_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ValueError("delivery_not_found")
            return self._decode(row)

    def pending(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                self._decode(r)
                for r in db.execute(
                    "SELECT * FROM deliveries WHERE conversation_state='queued' OR (conversation_state='committed' AND im_state='pending') ORDER BY created_at LIMIT 100"
                )
            ]

    def for_source_run(self, session_id: str, run_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT delivery_id, conversation_state, im_state, message_id, created_at,
                    json_extract(request_json,'$.target_session_id') AS target_session_id
                    FROM deliveries WHERE json_extract(request_json,'$.source_session_id')=?
                    AND json_extract(request_json,'$.source_run_id')=? ORDER BY created_at LIMIT 100""",
                    (session_id, run_id),
                )
            ]

    def bind_first_target(self, delivery_id: str, target: str, destination: str | None) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE deliveries SET request_json=json_set(request_json,
                   '$.target_session_id', ?, '$.destination_id', ?), im_state=?
                   WHERE delivery_id=? AND conversation_state='queued'
                   AND json_extract(request_json,'$.target_session_id')=''""",
                (target, destination, "pending" if destination else "not_requested", delivery_id),
            )

    def recent(self, source_session_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                {
                    key: value
                    for key, value in self._decode(row).items()
                    if key not in {"request", "fingerprint", "operation_key"}
                }
                for row in db.execute(
                    "SELECT * FROM deliveries WHERE json_extract(request_json,'$.source_session_id')=? ORDER BY created_at DESC LIMIT ?",
                    (source_session_id, limit),
                )
            ]

    def cancel_source(self, source_session_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE deliveries SET conversation_state=CASE WHEN conversation_state='queued' THEN 'cancelled' ELSE conversation_state END, im_state=CASE WHEN im_state='pending' THEN 'cancelled' ELSE im_state END,error='contact_paused' WHERE json_extract(request_json,'$.source_session_id')=? AND (conversation_state='queued' OR im_state='pending')",
                (source_session_id,),
            )

    def update(self, delivery_id: str, **values: Any) -> None:
        allowed = {"conversation_state", "im_state", "message_id", "provider_receipt", "error"}
        if not values or not set(values) <= allowed:
            raise ValueError("invalid delivery update")
        assignments = ",".join(f"{k}=?" for k in values)
        with self.connection() as db:
            db.execute(
                f"UPDATE deliveries SET {assignments} WHERE delivery_id=?",
                (*values.values(), delivery_id),
            )

    def forget_session(self, session_id: str) -> None:
        """Drop retained prepared content when either owning conversation is deleted."""
        with self.connection() as db:
            db.execute(
                "DELETE FROM deliveries WHERE json_extract(request_json,'$.source_session_id')=? "
                "OR json_extract(request_json,'$.target_session_id')=?",
                (session_id, session_id),
            )

    def claim_im(self, delivery_id: str) -> bool:
        with self.connection() as db:
            return (
                db.execute(
                    "UPDATE deliveries SET im_state='sending' WHERE delivery_id=? AND conversation_state='committed' AND im_state='pending'",
                    (delivery_id,),
                ).rowcount
                == 1
            )
