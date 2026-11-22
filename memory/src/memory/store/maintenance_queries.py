"""Ordinary-only queries and durable audit state on the existing SQLite connection."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from ..maintenance.models import ACTIONS, MaintenanceError, canonical, fact, revision

ORDINARY = "owner_user_id=? AND memory_class='normal' AND pinned=0"
ACTIVE = "deleted_at IS NULL AND valid_until IS NULL"


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class MaintenanceQueries:
    def __init__(self, connection: sqlite3.Connection, lock: AbstractContextManager[Any]) -> None:
        self.conn = connection
        self.lock = lock

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.conn:
            # Serialize against other processes as well as this Store's worker.
            self.conn.execute("BEGIN IMMEDIATE")
            yield self.conn

    def current(self, owner: str, semantic_id: str, *, active: bool = False) -> dict[str, Any]:
        row = self.conn.execute(
            f"SELECT * FROM semantic WHERE {ORDINARY} AND id=?"
            + (f" AND {ACTIVE}" if active else ""),
            (owner, semantic_id),
        ).fetchone()
        if row is None:
            raise MaintenanceError("ordinary_fact_not_found", semantic_id)
        return fact(row)

    def sources(self, owner: str, semantic_id: str) -> list[str]:
        value = self.current(owner, semantic_id)
        refs = set(value["provenance"])
        for row in self.conn.execute(
            "SELECT sc.provenance FROM semantic_change sc JOIN semantic_change_member m "
            "ON m.change_id=sc.id WHERE m.semantic_id=? AND sc.owner_user_id=?",
            (semantic_id, owner),
        ):
            refs.update(json.loads(row[0]))
        return sorted(ref for ref in refs if isinstance(ref, str))

    def detail(
        self, owner: str, semantic_id: str, *, offset: int = 0, limit: int = 4000
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 8000:
            raise MaintenanceError("invalid_content_page")
        with self.lock:
            value = self.current(owner, semantic_id)
            text = str(value["statement"])
            return {
                **value,
                "revision": revision(value),
                "statement": text[offset : offset + limit],
                "content_offset": offset,
                "content_length": len(text),
                "next_content_offset": offset + limit if offset + limit < len(text) else None,
                "source_refs": self.sources(owner, semantic_id),
                "state": "revoked"
                if value["deleted_at"]
                else "retired"
                if value["valid_until"]
                else "active",
            }

    def query(
        self, owner: str, *, query: str = "", after_id: str = "", limit: int = 20
    ) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            raise MaintenanceError("invalid_page_size")
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM semantic WHERE {ORDINARY} AND {ACTIVE} "
                "AND id>? AND instr(lower(statement),lower(?))>0 ORDER BY id LIMIT ?",
                (owner, after_id, query, limit + 1),
            ).fetchall()
            values = [fact(row) for row in rows[:limit]]
            return {
                "items": [self.preview(value) for value in values],
                "next_after_id": values[-1]["id"] if len(rows) > limit else None,
                "scope": "active_ordinary",
            }

    @staticmethod
    def preview(value: dict[str, Any]) -> dict[str, Any]:
        text = str(value["statement"])
        return {
            "semantic_id": value["id"],
            "statement": text[:500],
            "statement_truncated": len(text) > 500,
            "revision": revision(value),
            "speaker_origin": value.get("speaker_origin"),
        }

    def audit(self, owner: str, audit_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM semantic_audit WHERE id=? AND owner_user_id=?", (audit_id, owner)
        ).fetchone()
        if row is None:
            raise MaintenanceError("audit_not_found")
        return cast(sqlite3.Row, row)

    def start(
        self, owner: str, session_id: str, *, query: str = "", semantic_ids: list[str] | None = None
    ) -> dict[str, Any]:
        if not session_id:
            raise MaintenanceError("host_session_required")
        if semantic_ids is not None and (not semantic_ids or len(semantic_ids) > 200):
            raise MaintenanceError("invalid_selection")
        with self.transaction():
            if semantic_ids is not None:
                values = [
                    self.current(owner, sid, active=True) for sid in sorted(set(semantic_ids))
                ]
            else:
                values = [
                    fact(row)
                    for row in self.conn.execute(
                        f"SELECT * FROM semantic WHERE {ORDINARY} AND {ACTIVE} "
                        "AND instr(lower(statement),lower(?))>0 ORDER BY id",
                        (owner, query),
                    )
                ]
            audit_id = str(uuid4())
            scope = {
                "query": query,
                "semantic_ids": semantic_ids,
                "selection": "ids" if semantic_ids else "query" if query else "all",
            }
            self.conn.execute(
                "INSERT INTO semantic_audit VALUES(?,?,?,?,?)",
                (audit_id, owner, session_id, canonical(scope), timestamp()),
            )
            self.conn.executemany(
                "INSERT INTO semantic_audit_item(audit_id,semantic_id,ordinal,snapshot_json,revision) VALUES(?,?,?,?,?)",
                [
                    (audit_id, value["id"], index, canonical(value), revision(value))
                    for index, value in enumerate(values)
                ],
            )
        return self.status(owner, audit_id)

    def audits(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            return [
                self.status(owner, row[0])
                for row in self.conn.execute(
                    "SELECT id FROM semantic_audit WHERE owner_user_id=? ORDER BY created_at DESC LIMIT ?",
                    (owner, min(50, max(1, limit))),
                ).fetchall()
            ]

    def status(self, owner: str, audit_id: str) -> dict[str, Any]:
        with self.lock:
            audit = self.audit(owner, audit_id)
            rows = self.conn.execute(
                "SELECT read_at,decision_json FROM semantic_audit_item WHERE audit_id=?",
                (audit_id,),
            ).fetchall()
            decisions = [json.loads(row["decision_json"]) for row in rows if row["decision_json"]]
            counts = {action: sum(d["action"] == action for d in decisions) for action in ACTIONS}
            applied = self.conn.execute(
                "SELECT receipt_json FROM semantic_maintenance_batch WHERE audit_id=? AND receipt_json IS NOT NULL",
                (audit_id,),
            ).fetchall()
            return {
                "audit_id": audit_id,
                "session_id": audit["session_id"],
                "created_at": audit["created_at"],
                "scope": json.loads(audit["scope_json"]),
                "total": len(rows),
                "read": sum(row["read_at"] is not None for row in rows),
                "reviewed": len(decisions),
                "remaining": len(rows) - len(decisions),
                "unverified": sum(d["source_status"] != "verified" for d in decisions),
                "decisions": counts,
                "coverage_complete": len(decisions) == len(rows),
                "committed_batches": len(applied),
                "verified_changes": sum(len(json.loads(row[0])["changes"]) for row in applied),
            }

    def audit_page(
        self,
        owner: str,
        audit_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
        pending_only: bool = True,
    ) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 50:
            raise MaintenanceError("invalid_page")
        with self.transaction():
            self.audit(owner, audit_id)
            rows = self.conn.execute(
                "SELECT * FROM semantic_audit_item WHERE audit_id=? AND ordinal>=? "
                + ("AND decision_json IS NULL " if pending_only else "")
                + "ORDER BY ordinal LIMIT ?",
                (audit_id, offset, limit + 1),
            ).fetchall()
            items = []
            for row in rows[:limit]:
                value = json.loads(row["snapshot_json"])
                try:
                    changed = revision(self.current(owner, row["semantic_id"])) != row["revision"]
                except MaintenanceError:
                    changed = True
                items.append(
                    {
                        **self.preview(value),
                        "changed_since_snapshot": changed,
                        "decision": json.loads(row["decision_json"])
                        if row["decision_json"]
                        else None,
                    }
                )
                self.conn.execute(
                    "UPDATE semantic_audit_item SET read_at=? WHERE audit_id=? AND semantic_id=?",
                    (timestamp(), audit_id, row["semantic_id"]),
                )
            return {
                "audit_id": audit_id,
                "items": items,
                "next_offset": rows[limit - 1]["ordinal"] + 1 if len(rows) > limit else None,
            }

    def refresh(self, owner: str, audit_id: str, semantic_ids: list[str]) -> dict[str, Any]:
        with self.transaction():
            self.audit(owner, audit_id)
            for sid in semantic_ids:
                value = self.current(owner, sid)
                cursor = self.conn.execute(
                    "UPDATE semantic_audit_item SET snapshot_json=?,revision=?,decision_json=NULL,reviewed_at=NULL,read_at=NULL "
                    "WHERE audit_id=? AND semantic_id=?",
                    (canonical(value), revision(value), audit_id, sid),
                )
                if cursor.rowcount != 1:
                    raise MaintenanceError("fact_outside_audit", sid)
        return self.status(owner, audit_id)

    def record(
        self,
        owner: str,
        audit_id: str,
        decisions: list[dict[str, Any]],
        *,
        user_source_ref: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(decisions) <= 50:
            raise MaintenanceError("invalid_decision_count")
        if len({d.get("semantic_id") for d in decisions}) != len(decisions):
            raise MaintenanceError("duplicate_decision")
        with self.transaction():
            self.audit(owner, audit_id)
            for decision in decisions:
                sid = decision.get("semantic_id")
                row = self.conn.execute(
                    "SELECT * FROM semantic_audit_item WHERE audit_id=? AND semantic_id=?",
                    (audit_id, sid),
                ).fetchone()
                if row is None:
                    raise MaintenanceError("fact_outside_audit")
                current = self.current(owner, str(sid))
                if revision(current) != row["revision"]:
                    raise MaintenanceError("stale_audit_item", str(sid))
                action = decision.get("action")
                if action not in ACTIONS or not str(decision.get("reason", "")).strip():
                    raise MaintenanceError("invalid_decision")
                if decision.get("source_status") not in {"verified", "unverified"}:
                    raise MaintenanceError("invalid_source_status")
                refs = decision.get("evidence_refs", [])
                allowed = set(self.sources(owner, str(sid)))
                if user_source_ref:
                    allowed.add(user_source_ref)
                if not isinstance(refs, list) or not all(
                    isinstance(ref, str) and ref in allowed for ref in refs
                ):
                    raise MaintenanceError("invalid_evidence_ref")
                if decision["source_status"] == "verified" and not refs:
                    raise MaintenanceError("evidence_required")
                if action == "rewrite" and (
                    not str(decision.get("proposed_statement", "")).strip()
                    or decision.get("old_disposition") not in {"retire", "revoke"}
                ):
                    raise MaintenanceError("rewrite_requires_text_and_disposition")
                if action == "merge" and (
                    not decision.get("merge_into") or decision["merge_into"] == sid
                ):
                    raise MaintenanceError("merge_target_required")
                self.conn.execute(
                    "UPDATE semantic_audit_item SET decision_json=?,reviewed_at=? WHERE audit_id=? AND semantic_id=?",
                    (canonical(decision), timestamp(), audit_id, sid),
                )
        return self.status(owner, audit_id)
