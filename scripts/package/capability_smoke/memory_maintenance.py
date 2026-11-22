"""Offline inventory, source, approval, mutation and builtin-Skill capability probe."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def run_memory_maintenance_smoke() -> dict[str, bool]:
    from agent.approvals import ApprovalBroker
    from agent.command_runtime.models import CommandApprovalDecision
    from agent.memory_maintenance.dispatch import INVOCATION_ARG
    from agent.memory_maintenance.service import MaintenanceInvocation
    from agent.sessions.changes import SessionChangeBroker, SessionRevisionStore
    from agent.sessions.store import NotifyingSessionStore
    from agent.skills import SkillCatalogService
    from memory.maintenance.service import SemanticMaintenance

    from agent import Event, JsonlSessionStore, Session, make_default_registry
    from memory import SemanticRow, SqliteMemoryStore

    with TemporaryDirectory(prefix="af-memory-maintenance-smoke-") as temp:
        root = Path(temp)
        store = SqliteMemoryStore(root / "memory.db")
        revisions = SessionRevisionStore(root / "revisions.db")
        try:
            sessions = NotifyingSessionStore(
                JsonlSessionStore(root / "sessions"), SessionChangeBroker(revisions)
            )
            sessions.create(Session.new("maintenance", "default", "test/model", session_id="smoke"))
            now = datetime.now(UTC)
            sessions.append_event(
                "smoke",
                Event(
                    type="user_message",
                    uuid="source",
                    ts=now,
                    payload={"content": "The temporary instruction applied once."},
                ),
            )
            for sid, pinned in (("ordinary", False), ("pinned", True)):
                store.add_semantic(
                    SemanticRow(
                        id=sid,
                        statement="incorrect durable claim",
                        persona_id="default",
                        created_at=now,
                        updated_at=now,
                        pinned=pinned,
                        provenance=["smoke#source"],
                    )
                )
            memory = SemanticMaintenance(store, owner_user_id="local")
            broker = ApprovalBroker()
            approvals = []

            def approve(value: dict[str, object]) -> None:
                if value["status"] == "pending":
                    approvals.append(value)
                    broker.decide(
                        str(value["request_id"]),
                        CommandApprovalDecision(allow=True),
                        session_id="smoke",
                        run_id="run",
                        expected_kind="memory_maintenance",
                        lifetime="once",
                    )

            broker.add_listener(approve)
            registry = make_default_registry(
                sessions,
                memory_maintenance=memory,
                approval_broker=broker,
                tavily_api_key="",
                amap_webservice_key="",
                qweather_api_key="",
                qweather_api_host="",
                stderr_warn=False,
            )
            invocation = MaintenanceInvocation(
                "smoke", "run", "call", "desktop_chat", "smoke#source"
            )

            def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
                result = registry.invoke(name, {**args, INVOCATION_ARG: invocation})
                if result.is_error:
                    raise RuntimeError(result.text)
                return json.loads(result.text)

            inventory = call("semantic_memory_query", {})
            assert [v["semantic_id"] for v in inventory["items"]] == ["ordinary"]
            evidence = call(
                "semantic_memory_sources", {"semantic_id": "ordinary", "source_ref": "smoke#source"}
            )
            assert (
                evidence["status"] == "available" and evidence["messages"][0]["speaker"] == "user"
            )
            aid = call("semantic_memory_audit", {"action": "start"})["audit_id"]
            call(
                "semantic_memory_audit",
                {
                    "action": "record",
                    "audit_id": aid,
                    "decisions": [
                        {
                            "semantic_id": "ordinary",
                            "action": "rewrite",
                            "reason": "wrong scope",
                            "source_status": "verified",
                            "evidence_refs": ["smoke#source"],
                            "old_disposition": "revoke",
                            "proposed_statement": "verified replacement fact",
                        }
                    ],
                },
            )
            batch = call(
                "semantic_memory_changes",
                {"action": "prepare", "audit_id": aid, "semantic_ids": ["ordinary"]},
            )
            receipt = call(
                "semantic_memory_changes", {"action": "apply", "batch_id": batch["batch_id"]}
            )
            replay = call(
                "semantic_memory_changes", {"action": "apply", "batch_id": batch["batch_id"]}
            )
            assert receipt["changes"] == replay["changes"] and len(approvals) == 1
            new_id = receipt["changes"][0]["new_id"]
            assert call("semantic_memory_detail", {"semantic_id": "ordinary"})["state"] == "revoked"
            assert call("semantic_memory_detail", {"semantic_id": new_id})["state"] == "active"
            assert (
                store.search_semantic(
                    "replacement", owner_user_id="local", persona_id="default", limit=10
                )[0][0].id
                == new_id
            )
            skills = SkillCatalogService(
                personal_root=root / "skills", shared_root=root / "shared", watcher_enabled=False
            )
            snapshot = skills.initialize()
            skill = next(s for s in snapshot.entries if s.name == "maintain-af-memory")
            assert "semantic_memory_query" in (
                skill.package_root / "references/inspect.md"
            ).read_text(encoding="utf-8")
            assert "semantic_memory_changes" in (
                skill.package_root / "references/correct.md"
            ).read_text(encoding="utf-8")
            return {
                "ordinary_inventory": True,
                "direct_source": True,
                "approved_atomic_rewrite": True,
                "durable_receipt": True,
                "fts_readback": True,
                "builtin_skill": True,
            }
        finally:
            store.close()
