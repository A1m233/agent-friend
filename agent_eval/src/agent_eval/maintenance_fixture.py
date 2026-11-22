"""Seed real maintenance services; no fake maintenance tools or user database access."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.approvals import ApprovalBroker
from agent.command_runtime.models import CommandApprovalDecision
from memory.maintenance.service import SemanticMaintenance

from agent import Event, JsonlSessionStore, Session
from memory import SemanticRow, SqliteMemoryStore

from .schema import FixtureSemanticMemory

LOCAL_TOOL_NAMES = frozenset(
    {
        "semantic_memory_query",
        "semantic_memory_detail",
        "semantic_memory_sources",
        "semantic_memory_audit",
        "semantic_memory_changes",
        "skill_read",
        "filesystem_read_text",
        "filesystem_list_directory",
        "recall_past_chats",
        "list_conversations",
        "read_conversation",
        "read_inner_activity",
        "recover_tool_result",
        "inspect_tool_calls",
        "calculate",
        "datetime_calculate",
    }
)


def parse_fixture(raw: Any, *, label: str) -> list[FixtureSemanticMemory] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError(f"{label} must be a list of at most 100 facts")
    output = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {
            "id",
            "statement",
            "source_text",
            "speaker",
            "pinned",
        }:
            raise ValueError(f"{label}: invalid fact fields")
        if not all(
            isinstance(item.get(key), str) and item[key].strip() for key in ("id", "statement")
        ):
            raise ValueError(f"{label}: id and statement required")
        if item.get("source_text") is not None and not isinstance(item["source_text"], str):
            raise ValueError(f"{label}: source_text must be text or null")
        if item.get("speaker", "user") not in {"user", "assistant"} or not isinstance(
            item.get("pinned", False), bool
        ):
            raise ValueError(f"{label}: invalid speaker or pinned")
        output.append(FixtureSemanticMemory(**item))
    if len({f.id for f in output}) != len(output):
        raise ValueError(f"{label}: duplicate fact ID")
    return output


def build_fixture(
    root: Path, sessions: JsonlSessionStore, facts: list[FixtureSemanticMemory]
) -> tuple[SqliteMemoryStore, SemanticMaintenance, ApprovalBroker]:
    store = SqliteMemoryStore(root / "semantic-fixture.db")
    service = SemanticMaintenance(store, owner_user_id="local")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    source_session = "semantic-fixture-sources"
    sessions.create(
        Session.new(
            "长期记忆原始来源", "default", "test/model", session_id=source_session, created_at=now
        )
    )
    for index, fact in enumerate(facts):
        source_id = f"fact-source-{index}"
        refs = (
            [f"{source_session}#{source_id}"]
            if fact.source_text is not None
            else [f"legacy-episode-{index}"]
        )
        if fact.source_text is not None:
            sessions.append_event(
                source_session,
                Event(
                    type="user_message" if fact.speaker == "user" else "assistant_message",
                    uuid=source_id,
                    ts=now,
                    payload={"content": fact.source_text},
                ),
            )
        store.add_semantic(
            SemanticRow(
                id=fact.id,
                statement=fact.statement,
                persona_id="default",
                created_at=now,
                updated_at=now,
                provenance=refs,
                pinned=fact.pinned,
                speaker_origin="user" if fact.speaker == "user" else "agent",
            )
        )
    broker = ApprovalBroker()

    # This fixture represents the explicit user's one-batch approval. We still
    # traverse the real broker lifecycle; tests inspect the exact committed batch.
    def decide(value: dict[str, object]) -> None:
        if value["status"] == "pending":
            if value["kind"] != "memory_maintenance":
                broker.cancel_run(str(value["session_id"]), str(value["run_id"]))
                return
            broker.decide(
                str(value["request_id"]),
                CommandApprovalDecision(allow=True),
                session_id=str(value["session_id"]),
                run_id=str(value["run_id"]),
                expected_kind="memory_maintenance",
                lifetime="once",
            )

    broker.add_listener(decide)
    return store, service, broker
