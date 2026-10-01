"""记忆库 schema：建表 DDL、行 dataclass、时间序列化辅助。

设计原则：**一次把 exploration §5 / design §4.2 的预留列建全**，让 Reflection /
forget / 多 user-persona / backfill / 冲突解决等未来能力只需新增实现、不改表结构。

详见 docs/requirements/008-engine-memory/design.md §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

__all__ = [
    "DDL_STATEMENTS",
    "SCHEMA_VERSION",
    "EpisodicRow",
    "MemoryClass",
    "SemanticChangeMemberRow",
    "SemanticChangeRow",
    "SemanticRow",
    "parse_ts",
    "serialize_ts",
]

SCHEMA_VERSION = 8
"""记忆库 schema 版本。

- v1：semantic/episodic 表，FTS5 `tokenize='trigram'` 直接索引原文列
- v2（pass-1 / 013）：semantic 加 `statement_tokens` 列、episodic 加 `summary_tokens` 列，
  FTS5 改用 `tokenize='unicode61'` 索引 jieba 切词后空格连接的 tokens 列；中文召回从
  3-字滑窗换成"按词"匹配。详见 docs/requirements/013-memory-quality-pass-1/design.md §4。
- v3（048 M48.2）：semantic 增加 memory_class / fact_key / supersedes_id。
- v4（048 M48.3）：增加 embedding generation / manifest 派生索引元数据。
- v5（084）：增加独立、版本化 Core Profile 与 extraction commit。
- v6（086）：增加普通 Semantic 多对多 change/member 谱系与抽取输出契约版本。
- v7（136）：增加可恢复的待抽取素材，冷却时间保存在 schema_meta。
- v8（147）：待抽取素材增加 blocked 状态、错误码与隔离时间。
"""

MemoryClass = Literal["core", "priority", "normal"]


def serialize_ts(dt: datetime | None) -> str | None:
    """datetime → UTC ISO8601（带 ``Z``）；``None`` 透传。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_ts(s: str | None) -> datetime | None:
    """UTC ISO8601 → datetime；``None`` 透传。"""
    if s is None:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class SemanticRow:
    """语义记忆行：事实 / 偏好 / 关系。

    召回有效性：仅 ``deleted_at IS NULL AND valid_until IS NULL`` 的行参与召回。
    """

    id: str
    statement: str
    persona_id: str
    created_at: datetime
    updated_at: datetime
    importance: float = 0.5
    pinned: bool = False
    source: str = "extracted"  # extracted | reflected（Reflection 预留）
    speaker_origin: str = "user"  # user | agent（来源权重）
    valid_from: datetime | None = None
    valid_until: datetime | None = None  # 非空 = 已被取代
    provenance: list[str] = field(default_factory=list)  # episode_id 列表
    deleted_at: datetime | None = None  # 非空 = 软删（forget 预留）
    owner_user_id: str = "local"
    memory_class: MemoryClass = "normal"
    fact_key: str | None = None
    supersedes_id: str | None = None


@dataclass
class EpisodicRow:
    """情节记忆行：对一段对话的认知摘要（不存原文，存指针）。"""

    id: str
    summary: str
    source_ref: str  # "{session_id}#{start_uuid}..{end_uuid}"
    persona_id: str
    occurred_at: datetime
    created_at: datetime
    importance: float = 0.5
    participants: list[str] = field(default_factory=list)
    deleted_at: datetime | None = None
    owner_user_id: str = "local"


@dataclass(frozen=True)
class SemanticChangeMemberRow:
    """One introduced or retired member of a Semantic change event."""

    semantic_id: str
    role: Literal["introduced", "retired"]
    reason: str
    statement: str = ""
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True)
class SemanticChangeRow:
    """Diagnostic projection of one atomic Semantic change event."""

    id: str
    observation_id: str | None
    owner_user_id: str
    persona_id: str
    origin: str
    provenance: tuple[str, ...]
    created_at: datetime
    output_contract_version: int | None = None
    members: tuple[SemanticChangeMemberRow, ...] = ()


DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS extraction_pending (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        source_refs TEXT NOT NULL,
        reason TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        error_code TEXT,
        blocked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic (
        id               TEXT PRIMARY KEY,
        statement        TEXT NOT NULL,
        statement_tokens TEXT NOT NULL DEFAULT '',
        importance       REAL NOT NULL DEFAULT 0.5,
        pinned           INTEGER NOT NULL DEFAULT 0,
        source           TEXT NOT NULL DEFAULT 'extracted',
        speaker_origin   TEXT NOT NULL DEFAULT 'user',
        memory_class     TEXT NOT NULL DEFAULT 'normal',
        fact_key         TEXT,
        supersedes_id    TEXT,
        valid_from       TEXT,
        valid_until      TEXT,
        provenance       TEXT NOT NULL DEFAULT '[]',
        deleted_at       TEXT,
        owner_user_id    TEXT NOT NULL DEFAULT 'local',
        persona_id       TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episodic (
        id             TEXT PRIMARY KEY,
        summary        TEXT NOT NULL,
        summary_tokens TEXT NOT NULL DEFAULT '',
        source_ref     TEXT NOT NULL,
        importance     REAL NOT NULL DEFAULT 0.5,
        participants   TEXT NOT NULL DEFAULT '[]',
        occurred_at    TEXT NOT NULL,
        deleted_at     TEXT,
        owner_user_id  TEXT NOT NULL DEFAULT 'local',
        persona_id     TEXT NOT NULL,
        created_at     TEXT NOT NULL
    )
    """,
    # FTS5 影子表：external content，rowid 关联主表 rowid，索引 jieba 切词后的
    # ``*_tokens`` 列。tokenize='unicode61' 把空格分隔的 token 当词索引（每个
    # jieba 词成为一个 FTS5 token），中文召回按词匹配而不是 3-字滑窗。详见
    # docs/requirements/013-memory-quality-pass-1/design.md §4。
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts
    USING fts5(statement_tokens, content='semantic', content_rowid='rowid', tokenize='unicode61')
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts
    USING fts5(summary_tokens, content='episodic', content_rowid='rowid', tokenize='unicode61')
    """,
    # 常用过滤索引
    "CREATE INDEX IF NOT EXISTS idx_semantic_scope ON semantic(owner_user_id, persona_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_pinned ON semantic(pinned)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_class ON semantic(owner_user_id, memory_class)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_fact_key ON semantic(owner_user_id, fact_key)",
    "CREATE INDEX IF NOT EXISTS idx_episodic_scope ON episodic(owner_user_id, persona_id)",
    """
    CREATE TABLE IF NOT EXISTS core_profile_entry (
        id               TEXT PRIMARY KEY,
        owner_user_id    TEXT NOT NULL DEFAULT 'local',
        scope_kind       TEXT NOT NULL,
        persona_id       TEXT NOT NULL DEFAULT '',
        field_key        TEXT NOT NULL,
        value_json       TEXT NOT NULL,
        normalized_value TEXT NOT NULL,
        valid_from       TEXT NOT NULL,
        valid_until      TEXT,
        supersedes_id    TEXT,
        provenance       TEXT NOT NULL DEFAULT '[]',
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        deleted_at       TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_core_single_active
    ON core_profile_entry(owner_user_id, scope_kind, persona_id, field_key)
    WHERE valid_until IS NULL AND deleted_at IS NULL
      AND field_key IN (
        'user.preferred_name',
        'relationship.kind',
        'relationship.agent_nickname'
      )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_core_many_active
    ON core_profile_entry(owner_user_id, scope_kind, persona_id, field_key, normalized_value)
    WHERE valid_until IS NULL AND deleted_at IS NULL
      AND field_key = 'user.current_residences'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_core_scope
    ON core_profile_entry(owner_user_id, scope_kind, persona_id, field_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS extraction_commit (
        observation_id   TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        first_source_ref TEXT,
        last_source_ref  TEXT,
        output_sha256    TEXT NOT NULL,
        output_contract_version INTEGER NOT NULL DEFAULT 1,
        committed_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_change (
        id              TEXT PRIMARY KEY,
        observation_id  TEXT,
        owner_user_id   TEXT NOT NULL,
        persona_id      TEXT NOT NULL,
        origin          TEXT NOT NULL,
        provenance      TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_change_member (
        change_id       TEXT NOT NULL,
        semantic_id     TEXT NOT NULL,
        role            TEXT NOT NULL,
        reason          TEXT NOT NULL,
        PRIMARY KEY (change_id, semantic_id, role),
        FOREIGN KEY (change_id) REFERENCES semantic_change(id) ON DELETE CASCADE,
        FOREIGN KEY (semantic_id) REFERENCES semantic(id) ON DELETE RESTRICT,
        CHECK (role IN ('introduced', 'retired')),
        CHECK (length(trim(reason)) > 0)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_member_introduced_once
    ON semantic_change_member(semantic_id) WHERE role = 'introduced'
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_member_retired_once
    ON semantic_change_member(semantic_id) WHERE role = 'retired'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_semantic_change_scope
    ON semantic_change(owner_user_id, persona_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS embedding_generation (
        generation_id TEXT PRIMARY KEY,
        model_id       TEXT NOT NULL,
        dimension      INTEGER NOT NULL,
        status         TEXT NOT NULL,
        total_count    INTEGER NOT NULL DEFAULT 0,
        indexed_count  INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        last_error     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS embedding_manifest (
        generation_id TEXT NOT NULL,
        evidence_id   TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        indexed_at    TEXT NOT NULL,
        PRIMARY KEY(generation_id, evidence_id),
        FOREIGN KEY(generation_id) REFERENCES embedding_generation(generation_id)
            ON DELETE CASCADE
    )
    """,
)
