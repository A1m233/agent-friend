"""Frozen, owner-scoped Semantic batches and atomic receipts."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..maintenance.models import (
    MaintenanceAuthorization,
    MaintenanceError,
    canonical,
    digest,
    revision,
)
from .maintenance_queries import MaintenanceQueries, timestamp
from .schema import SemanticRow


class MaintenanceRepository(MaintenanceQueries):
    def batches(
        self, owner: str, audit_id: str, *, after_id: str = "", limit: int = 20
    ) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            raise MaintenanceError("invalid_page_size")
        with self.lock:
            self.audit(owner, audit_id)
            rows = self.conn.execute(
                "SELECT id,digest,committed_at FROM semantic_maintenance_batch "
                "WHERE owner_user_id=? AND audit_id=? AND id>? ORDER BY id LIMIT ?",
                (owner, audit_id, after_id, limit + 1),
            ).fetchall()
            return {
                "items": [dict(row) for row in rows[:limit]],
                "next_after_id": rows[limit - 1]["id"] if len(rows) > limit else None,
            }

    def batch(self, owner: str, batch_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM semantic_maintenance_batch WHERE id=? AND owner_user_id=?",
                (batch_id, owner),
            ).fetchone()
            if row is None:
                raise MaintenanceError("batch_not_found")
            return {
                "batch_id": batch_id,
                "audit_id": row["audit_id"],
                "session_id": row["session_id"],
                "digest": row["digest"],
                "payload": json.loads(row["payload_json"]),
                "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
            }

    def prepare(
        self,
        owner: str,
        session_id: str,
        audit_id: str,
        semantic_ids: list[str],
        *,
        user_source_ref: str | None = None,
    ) -> dict[str, Any]:
        if not session_id or not 1 <= len(semantic_ids) <= 100:
            raise MaintenanceError("invalid_batch_selection")
        if len(set(semantic_ids)) != len(semantic_ids):
            raise MaintenanceError("duplicate_batch_target")
        with self.transaction():
            self.audit(owner, audit_id)
            operations: list[dict[str, Any]] = []
            touched: set[str] = set()
            for sid in semantic_ids:
                row = self.conn.execute(
                    "SELECT * FROM semantic_audit_item WHERE audit_id=? AND semantic_id=?",
                    (audit_id, sid),
                ).fetchone()
                if row is None or not row["decision_json"]:
                    raise MaintenanceError("review_required", sid)
                decision = json.loads(row["decision_json"])
                action = decision["action"]
                if action in {"keep", "pending"}:
                    raise MaintenanceError("decision_is_not_a_change", sid)
                targets = [sid]
                if action == "merge":
                    targets.append(decision["merge_into"])
                snapshots = []
                for target in targets:
                    if target in touched:
                        raise MaintenanceError("overlapping_batch_changes", target)
                    touched.add(target)
                    previous = self.conn.execute(
                        "SELECT revision FROM semantic_audit_item WHERE audit_id=? AND semantic_id=?",
                        (audit_id, target),
                    ).fetchone()
                    if previous is None:
                        raise MaintenanceError("merge_target_outside_audit", target)
                    try:
                        value = self.current(owner, target, active=True)
                    except MaintenanceError as exc:
                        raise MaintenanceError("stale_audit_item", target) from exc
                    if revision(value) != previous["revision"]:
                        raise MaintenanceError("stale_audit_item", target)
                    snapshots.append(value)
                refs = set(decision.get("evidence_refs", []))
                for value in snapshots:
                    refs.update(self.sources(owner, value["id"]))
                if user_source_ref:
                    refs.add(user_source_ref)
                replacement = None
                if action in {"rewrite", "merge"}:
                    base = snapshots[-1] if action == "merge" else snapshots[0]
                    statement = decision.get("proposed_statement") or base["statement"]
                    if (
                        not isinstance(statement, str)
                        or not statement.strip()
                        or len(statement) > 16000
                    ):
                        raise MaintenanceError("invalid_replacement")
                    speaker = decision.get("speaker_origin", base["speaker_origin"])
                    if speaker not in {"user", "agent"}:
                        raise MaintenanceError("invalid_speaker_origin")
                    if len({v["persona_id"] for v in snapshots}) != 1:
                        raise MaintenanceError("cross_persona_merge")
                    replacement = {
                        "statement": statement.strip(),
                        "speaker_origin": speaker,
                        "persona_id": base["persona_id"],
                        "importance": base["importance"],
                    }
                operations.append(
                    {
                        "action": action,
                        "reason": decision["reason"],
                        "old_disposition": decision["old_disposition"]
                        if action == "rewrite"
                        else "revoke"
                        if action == "revoke"
                        else "retire",
                        "targets": [
                            {
                                "semantic_id": v["id"],
                                "revision": revision(v),
                                "statement": v["statement"],
                            }
                            for v in snapshots
                        ],
                        "replacement": replacement,
                        "source_refs": sorted(refs),
                    }
                )
            payload = {"audit_id": audit_id, "operations": operations}
            identity = digest(payload)
            existing = self.conn.execute(
                "SELECT id FROM semantic_maintenance_batch "
                "WHERE owner_user_id=? AND session_id=? AND audit_id=? AND digest=?",
                (owner, session_id, audit_id, identity),
            ).fetchone()
            if existing:
                batch_id = str(existing[0])
            else:
                batch_id = str(uuid4())
                self.conn.execute(
                    "INSERT INTO semantic_maintenance_batch"
                    "(id,owner_user_id,session_id,audit_id,digest,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        batch_id,
                        owner,
                        session_id,
                        audit_id,
                        identity,
                        canonical(payload),
                        timestamp(),
                    ),
                )
        return self.batch(owner, batch_id)

    def record_index_refresh(self, owner: str, batch_id: str, status: str) -> dict[str, Any]:
        if status not in {"disabled", "requested", "request_failed"}:
            raise MaintenanceError("invalid_index_refresh_status")
        with self.transaction():
            receipt = self.batch(owner, batch_id)["receipt"]
            if receipt is None:
                raise MaintenanceError("batch_not_committed")
            receipt["index_refresh"] = status
            self.conn.execute(
                "UPDATE semantic_maintenance_batch SET receipt_json=? WHERE id=? AND owner_user_id=?",
                (canonical(receipt), batch_id, owner),
            )
            return dict(receipt)

    def commit(self, owner: str, authorization: MaintenanceAuthorization) -> dict[str, Any]:
        # Import locally because the legacy transaction facade lives with SqliteMemoryStore.
        from .sqlite_store import ExtractionTransaction

        if not isinstance(authorization, MaintenanceAuthorization) or not authorization.request_id:
            raise MaintenanceError("host_authorization_required")
        with self.transaction():
            batch = self.batch(owner, authorization.batch_id)
            if batch["digest"] != authorization.batch_digest:
                raise MaintenanceError("batch_changed")
            if batch["session_id"] != authorization.session_id:
                raise MaintenanceError("authorization_session_mismatch")
            if batch["receipt"] is not None:
                return dict(batch["receipt"])
            operations = batch["payload"]["operations"]
            for operation in operations:
                for target in operation["targets"]:
                    try:
                        value = self.current(owner, target["semantic_id"], active=True)
                    except MaintenanceError as exc:
                        raise MaintenanceError("stale_batch_target", target["semantic_id"]) from exc
                    if revision(value) != target["revision"]:
                        raise MaintenanceError("stale_batch_target", target["semantic_id"])
            tx = ExtractionTransaction(self.conn)
            now = datetime.now(UTC)
            changes = []
            for operation in operations:
                change_id = str(uuid4())
                refs = list(operation["source_refs"])
                if authorization.source_ref and authorization.source_ref not in refs:
                    refs.append(authorization.source_ref)
                first = self.current(owner, operation["targets"][0]["semantic_id"])
                tx.add_semantic_change(
                    change_id=change_id,
                    observation_id=None,
                    owner_user_id=owner,
                    persona_id=first["persona_id"],
                    origin="maintenance",
                    provenance=refs,
                    created_at=now,
                )
                old_ids = []
                for target in operation["targets"]:
                    sid = target["semantic_id"]
                    revoke = operation["action"] == "revoke" or (
                        operation["action"] == "rewrite"
                        and operation["old_disposition"] == "revoke"
                    )
                    if revoke:
                        self.conn.execute(
                            "UPDATE semantic SET deleted_at=?,updated_at=? WHERE id=?",
                            (now.isoformat(), now.isoformat(), sid),
                        )
                    else:
                        tx.end_semantic(sid, now, written_at=now)
                    tx.add_semantic_change_member(
                        change_id=change_id,
                        semantic_id=sid,
                        role="retired",
                        reason=operation["reason"],
                    )
                    old_ids.append(sid)
                replacement = operation["replacement"]
                new_id = None
                if replacement is not None:
                    new_id = str(uuid4())
                    tx.add_semantic(
                        SemanticRow(
                            id=new_id,
                            statement=replacement["statement"],
                            persona_id=replacement["persona_id"],
                            owner_user_id=owner,
                            importance=replacement["importance"],
                            speaker_origin=replacement["speaker_origin"],
                            source="maintenance",
                            memory_class="normal",
                            pinned=False,
                            created_at=now,
                            updated_at=now,
                            valid_from=now,
                            provenance=refs,
                        )
                    )
                    tx.add_semantic_change_member(
                        change_id=change_id,
                        semantic_id=new_id,
                        role="introduced",
                        reason=operation["reason"],
                    )
                changed_ids = [*old_ids, *([new_id] if new_id else [])]
                readback = [self.current(owner, sid) for sid in changed_ids]
                changes.append(
                    {
                        "change_id": change_id,
                        "action": operation["action"],
                        "old_disposition": operation["old_disposition"],
                        "old_ids": old_ids,
                        "new_id": new_id,
                        "readback": readback,
                    }
                )
            epoch_key = "semantic_maintenance_epoch:" + owner
            row = self.conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (epoch_key,)
            ).fetchone()
            epoch = int(row[0]) + 1 if row else 1
            self.conn.execute(
                "INSERT INTO schema_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (epoch_key, str(epoch)),
            )
            receipt = {
                "batch_id": authorization.batch_id,
                "digest": authorization.batch_digest,
                "status": "committed",
                "committed_at": now.isoformat(),
                "authorization": asdict(authorization),
                "changes": changes,
                "maintenance_epoch": epoch,
                "index_refresh": "not_requested",
            }
            self.conn.execute(
                "UPDATE semantic_maintenance_batch SET receipt_json=?,committed_at=? WHERE id=?",
                (canonical(receipt), now.isoformat(), authorization.batch_id),
            )
            return receipt
