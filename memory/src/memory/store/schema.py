"""记忆库 schema：建表 DDL、行 dataclass、时间序列化辅助。

设计原则：**一次把 exploration §5 / design §4.2 的预留列建全**，让 Reflection /
forget / 多 user-persona / backfill / 冲突解决等未来能力只需新增实现、不改表结构。

详见 docs/requirements/008-engine-memory/design.md §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "DDL_STATEMENTS",
    "SCHEMA_VERSION",
    "EpisodicRow",
    "SemanticRow",
    "parse_ts",
    "serialize_ts",
]

SCHEMA_VERSION = 1
"""记忆库 schema 版本。未来真正改表时递增；v1 不做迁移逻辑。"""


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


DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic (
        id             TEXT PRIMARY KEY,
        statement      TEXT NOT NULL,
        importance     REAL NOT NULL DEFAULT 0.5,
        pinned         INTEGER NOT NULL DEFAULT 0,
        source         TEXT NOT NULL DEFAULT 'extracted',
        speaker_origin TEXT NOT NULL DEFAULT 'user',
        valid_from     TEXT,
        valid_until    TEXT,
        provenance     TEXT NOT NULL DEFAULT '[]',
        deleted_at     TEXT,
        owner_user_id  TEXT NOT NULL DEFAULT 'local',
        persona_id     TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS episodic (
        id             TEXT PRIMARY KEY,
        summary        TEXT NOT NULL,
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
    # FTS5 影子表：external content，rowid 关联主表 rowid。
    # 用 trigram 分词器——unicode61 默认把整段中文当一个 token，无法做中文关键词
    # 子串匹配；trigram 对中英文都做 3-gram 子串匹配，是中文为主场景的务实选择。
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts
    USING fts5(statement, content='semantic', content_rowid='rowid', tokenize='trigram')
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts
    USING fts5(summary, content='episodic', content_rowid='rowid', tokenize='trigram')
    """,
    # 常用过滤索引
    "CREATE INDEX IF NOT EXISTS idx_semantic_scope ON semantic(owner_user_id, persona_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_pinned ON semantic(pinned)",
    "CREATE INDEX IF NOT EXISTS idx_episodic_scope ON episodic(owner_user_id, persona_id)",
)
