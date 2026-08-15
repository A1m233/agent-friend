from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from memory.core_profile import CoreFieldKey, CoreScopeKind, normalize_core_value

from memory import CoreProfileEntry, SemanticRow, SqliteMemoryStore


def _entry(
    entry_id: str,
    *,
    field_key: CoreFieldKey,
    value: str,
    scope_kind: CoreScopeKind,
    persona_id: str = "",
) -> CoreProfileEntry:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return CoreProfileEntry(
        id=entry_id,
        owner_user_id="local",
        scope_kind=scope_kind,
        persona_id=persona_id,
        field_key=field_key,
        value=value,
        normalized_value=normalize_core_value(value),
        valid_from=now,
        valid_until=None,
        supersedes_id=None,
        provenance=(),
        created_at=now,
        updated_at=now,
    )


def test_user_fields_are_global_and_relationship_fields_are_persona_scoped() -> None:
    store = SqliteMemoryStore(":memory:")
    store.add_core_profile_entry(
        _entry(
            "name",
            field_key="user.preferred_name",
            value="小陈",
            scope_kind="user",
        )
    )
    store.add_core_profile_entry(
        _entry(
            "p1-relation",
            field_key="relationship.kind",
            value="知心朋友",
            scope_kind="persona",
            persona_id="p1",
        )
    )
    store.add_core_profile_entry(
        _entry(
            "p2-relation",
            field_key="relationship.kind",
            value="学习搭档",
            scope_kind="persona",
            persona_id="p2",
        )
    )

    p1 = store.core_profile(owner_user_id="local", persona_id="p1").as_dict()
    p2 = store.core_profile(owner_user_id="local", persona_id="p2").as_dict()
    assert (
        p1["user"]
        == p2["user"]
        == {
            "preferred_name": "小陈",
            "current_residences": [],
        }
    )
    assert p1["relationship"] == {"kind": "知心朋友", "agent_nickname": None}
    assert p2["relationship"] == {"kind": "学习搭档", "agent_nickname": None}


def test_v4_migration_maps_reliable_fields_and_keeps_ambiguous_singletons_ordinary(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = SqliteMemoryStore(db_path)
    now = datetime(2026, 8, 15, tzinfo=UTC)
    for row_id, statement, fact_key in (
        ("name-1", "用户名字是小陈", "user.name"),
        ("name-2", "用户名字是陈老师", "user.name"),
        ("home", "用户目前住在深圳", "user.residence"),
        ("agent-name", "AI名字是小月", "agent.name"),
    ):
        store.add_semantic(
            SemanticRow(
                id=row_id,
                statement=statement,
                persona_id="p1",
                created_at=now,
                updated_at=now,
                valid_from=now,
                fact_key=fact_key,
                memory_class="core",
                pinned=True,
            )
        )
    store._conn.execute("UPDATE schema_meta SET value = '4' WHERE key = 'schema_version'")
    store._conn.commit()
    store.close()

    migrated = SqliteMemoryStore(db_path)
    profile = migrated.core_profile(owner_user_id="local", persona_id="p1").as_dict()
    assert profile["user"] == {
        "preferred_name": None,
        "current_residences": ["深圳"],
    }
    assert profile["relationship"] == {"kind": None, "agent_nickname": "小月"}
    ordinary = {row.id: row for row in migrated.list_semantic(owner_user_id="local")}
    assert {"name-1", "name-2"}.issubset(ordinary)
    assert all(row.memory_class == "normal" and row.fact_key is None for row in ordinary.values())
