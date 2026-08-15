"""``SqliteMemoryStore``：episodic / semantic 的 SQLite 持久化 + FTS5 检索。

线程模型：异步抽取 worker 在后台线程写、对话主线程读。Python ``sqlite3``
连接对象本身不可跨线程并发使用，这里用 ``check_same_thread=False`` 开放跨线程，
并用一把 :class:`threading.RLock` 串行化所有访问（孵化期单进程，量小，足够）。
配合 WAL 模式让读写更顺。

详见 docs/requirements/008-engine-memory/design.md §4.3。

Pass-1（013）：FTS5 索引从 trigram 字符滑窗换成 jieba 词级 unicode61。详见
docs/requirements/013-memory-quality-pass-1/design.md §4。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import jieba

from ..core_profile import (
    CORE_FIELD_SPECS,
    CoreFieldKey,
    CoreProfile,
    CoreProfileEntry,
    CoreScopeKind,
    build_core_profile,
    normalize_core_value,
    validate_core_value,
)
from .schema import (
    DDL_STATEMENTS,
    SCHEMA_VERSION,
    EpisodicRow,
    SemanticRow,
    parse_ts,
    serialize_ts,
)

__all__ = ["ExtractionTransaction", "SqliteMemoryStore"]

logger = logging.getLogger(__name__)

_RELATED_DEFAULT_LIMIT = 200
"""``related_semantic`` 兜底返回的活跃事实上限。

孵化期单 user 单 persona 的活跃事实远小于此；超出再谈分页 / 更精的召回。"""


class ExtractionTransaction:
    """Write facade used only inside :meth:`SqliteMemoryStore.extraction_batch`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def core_is_active(self, entry_id: str) -> bool:
        return (
            self._conn.execute(
                """SELECT 1 FROM core_profile_entry
               WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL""",
                (entry_id,),
            ).fetchone()
            is not None
        )

    def semantic_is_active(self, semantic_id: str) -> bool:
        return (
            self._conn.execute(
                """SELECT 1 FROM semantic
               WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL""",
                (semantic_id,),
            ).fetchone()
            is not None
        )

    def add_core(self, row: CoreProfileEntry) -> None:
        validate_core_value(row.field_key, row.value)
        _insert_core_profile_entry(self._conn, row)

    def validate_core_profile(self, *, owner_user_id: str, persona_id: str) -> None:
        rows = self._conn.execute(
            """SELECT * FROM core_profile_entry
               WHERE owner_user_id = ?
                 AND (scope_kind = 'user' OR (scope_kind = 'persona' AND persona_id = ?))
                 AND valid_until IS NULL AND deleted_at IS NULL""",
            (owner_user_id, persona_id),
        ).fetchall()
        build_core_profile(
            [_to_core_profile_entry(row) for row in rows],
            owner_user_id=owner_user_id,
            persona_id=persona_id,
        )

    def end_core(self, entry_id: str, ended_at: datetime) -> bool:
        cur = self._conn.execute(
            """UPDATE core_profile_entry SET valid_until = ?, updated_at = ?
               WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL""",
            (serialize_ts(ended_at), serialize_ts(ended_at), entry_id),
        )
        return cur.rowcount == 1

    def add_semantic(self, row: SemanticRow) -> None:
        tokens = _tokenize(row.statement)
        cur = self._conn.execute(
            """INSERT INTO semantic
                   (id, statement, statement_tokens, importance, pinned, source,
                    speaker_origin, memory_class, fact_key, supersedes_id, valid_from,
                    valid_until, provenance, deleted_at, owner_user_id, persona_id,
                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.id,
                row.statement,
                tokens,
                row.importance,
                0,
                row.source,
                row.speaker_origin,
                "normal",
                None,
                row.supersedes_id,
                serialize_ts(row.valid_from),
                serialize_ts(row.valid_until),
                json.dumps(row.provenance, ensure_ascii=False),
                serialize_ts(row.deleted_at),
                row.owner_user_id,
                row.persona_id,
                serialize_ts(row.created_at),
                serialize_ts(row.updated_at),
            ),
        )
        self._conn.execute(
            "INSERT INTO semantic_fts(rowid, statement_tokens) VALUES (?, ?)",
            (cur.lastrowid, tokens),
        )

    def end_semantic(self, semantic_id: str, ended_at: datetime) -> bool:
        cur = self._conn.execute(
            """UPDATE semantic SET valid_until = ?, updated_at = ?
               WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL""",
            (serialize_ts(ended_at), serialize_ts(ended_at), semantic_id),
        )
        return cur.rowcount == 1

    def add_episodic(self, row: EpisodicRow) -> None:
        tokens = _tokenize(row.summary)
        cur = self._conn.execute(
            """INSERT INTO episodic
                   (id, summary, summary_tokens, source_ref, importance, participants,
                    occurred_at, deleted_at, owner_user_id, persona_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.id,
                row.summary,
                tokens,
                row.source_ref,
                row.importance,
                json.dumps(row.participants, ensure_ascii=False),
                serialize_ts(row.occurred_at),
                serialize_ts(row.deleted_at),
                row.owner_user_id,
                row.persona_id,
                serialize_ts(row.created_at),
            ),
        )
        self._conn.execute(
            "INSERT INTO episodic_fts(rowid, summary_tokens) VALUES (?, ?)",
            (cur.lastrowid, tokens),
        )


class SqliteMemoryStore:
    """记忆库的 SQLite 实现。

    Args:
        db_path: 库文件路径；父目录不存在会自动创建。``":memory:"`` 用于测试。
    """

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.RLock()
        is_mem = str(db_path) == ":memory:"
        if not is_mem:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        try:
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                previous_version = _schema_version(self._conn)
                # v1 → v2 迁移：在 DDL 跑之前 ALTER TABLE 加列 + DROP 旧 trigram FTS5；
                # 后续 DDL 的 CREATE IF NOT EXISTS 会用新 unicode61 配置建表，旧表
                # IF NOT EXISTS 不会改、必须先 DROP。
                need_backfill = _premigrate_schema(self._conn)
                logger.info(
                    "schema premigrate need_backfill=%s version=%d", need_backfill, SCHEMA_VERSION
                )
                for ddl in DDL_STATEMENTS:
                    self._conn.execute(ddl)
                if need_backfill:
                    _backfill_tokens_and_rebuild_fts(self._conn)
                    logger.info("schema backfill completed")
                if previous_version < 5:
                    _migrate_core_profile_v5(self._conn)
                self._conn.execute(
                    "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                self._conn.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(SCHEMA_VERSION),),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.exception("sqlite store initialization failed for %s", db_path)
            raise

    def warmup(self) -> None:
        """Warm tokenizer and SQLite read path without writing user data."""
        _tokenize("语音通话 warmup")
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()

    @contextmanager
    def extraction_batch(
        self,
        *,
        observation_id: str,
        session_id: str,
        first_source_ref: str | None,
        last_source_ref: str | None,
        output_sha256: str,
        committed_at: datetime,
    ) -> Iterator[ExtractionTransaction | None]:
        """Open the all-or-nothing/idempotent boundary for one extraction output."""
        with self._lock:
            duplicate = self._conn.execute(
                "SELECT 1 FROM extraction_commit WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
            if duplicate is not None:
                yield None
                return
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield ExtractionTransaction(self._conn)
                self._conn.execute(
                    """INSERT INTO extraction_commit
                           (observation_id, session_id, first_source_ref, last_source_ref,
                            output_sha256, committed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        observation_id,
                        session_id,
                        first_source_ref,
                        last_source_ref,
                        output_sha256,
                        serialize_ts(committed_at),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ----- 写 -----

    def add_core_profile_entry(self, row: CoreProfileEntry) -> None:
        """Insert one validated Core Profile version."""
        validate_core_value(row.field_key, row.value)
        try:
            with self._lock:
                _insert_core_profile_entry(self._conn, row)
                rows = self._conn.execute(
                    """SELECT * FROM core_profile_entry
                       WHERE owner_user_id = ?
                         AND (scope_kind = 'user' OR (scope_kind = 'persona' AND persona_id = ?))
                         AND valid_until IS NULL AND deleted_at IS NULL""",
                    (row.owner_user_id, row.persona_id),
                ).fetchall()
                build_core_profile(
                    [_to_core_profile_entry(item) for item in rows],
                    owner_user_id=row.owner_user_id,
                    persona_id=row.persona_id,
                )
                self._conn.commit()
        except (sqlite3.Error, ValueError):
            self._conn.rollback()
            logger.exception("add_core_profile_entry failed id=%s", row.id)
            raise

    def supersede_and_add_core_profile_entry(
        self, old_id: str, row: CoreProfileEntry, valid_until: datetime
    ) -> bool:
        """Atomically replace one active Core Profile entry."""
        validate_core_value(row.field_key, row.value)
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    UPDATE core_profile_entry SET valid_until = ?, updated_at = ?
                    WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL
                    """,
                    (serialize_ts(valid_until), serialize_ts(valid_until), old_id),
                )
                if cur.rowcount == 0:
                    self._conn.rollback()
                    return False
                _insert_core_profile_entry(self._conn, row)
                rows = self._conn.execute(
                    """SELECT * FROM core_profile_entry
                       WHERE owner_user_id = ?
                         AND (scope_kind = 'user' OR (scope_kind = 'persona' AND persona_id = ?))
                         AND valid_until IS NULL AND deleted_at IS NULL""",
                    (row.owner_user_id, row.persona_id),
                ).fetchall()
                build_core_profile(
                    [_to_core_profile_entry(item) for item in rows],
                    owner_user_id=row.owner_user_id,
                    persona_id=row.persona_id,
                )
                self._conn.commit()
                return True
        except (sqlite3.Error, ValueError):
            self._conn.rollback()
            logger.exception("supersede_and_add_core_profile_entry failed old=%s", old_id)
            raise

    def remove_core_profile_entry(self, old_id: str, valid_until: datetime) -> bool:
        """End one active Core Profile value without creating a replacement."""
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE core_profile_entry SET valid_until = ?, updated_at = ?
                WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL
                """,
                (serialize_ts(valid_until), serialize_ts(valid_until), old_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def add_semantic(self, row: SemanticRow) -> None:
        """插入一条语义记忆，同步写 FTS。"""
        tokens = _tokenize(row.statement)
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    INSERT INTO semantic
                        (id, statement, statement_tokens, importance, pinned, source, speaker_origin,
                         memory_class, fact_key, supersedes_id, valid_from, valid_until,
                         provenance, deleted_at, owner_user_id, persona_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.id,
                        row.statement,
                        tokens,
                        row.importance,
                        1 if row.pinned else 0,
                        row.source,
                        row.speaker_origin,
                        row.memory_class,
                        row.fact_key,
                        row.supersedes_id,
                        serialize_ts(row.valid_from),
                        serialize_ts(row.valid_until),
                        json.dumps(row.provenance, ensure_ascii=False),
                        serialize_ts(row.deleted_at),
                        row.owner_user_id,
                        row.persona_id,
                        serialize_ts(row.created_at),
                        serialize_ts(row.updated_at),
                    ),
                )
                self._conn.execute(
                    "INSERT INTO semantic_fts(rowid, statement_tokens) VALUES (?, ?)",
                    (cur.lastrowid, tokens),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.exception("add_semantic failed id=%s owner=%s", row.id, row.owner_user_id)
            raise

    def supersede_and_add_semantic(
        self,
        old_id: str,
        row: SemanticRow,
        valid_until: datetime,
    ) -> bool:
        """在同一事务内失效旧版本并插入新版本。"""
        tokens = _tokenize(row.statement)
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    UPDATE semantic SET valid_until = ?, updated_at = ?
                    WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL
                    """,
                    (serialize_ts(valid_until), serialize_ts(valid_until), old_id),
                )
                if cur.rowcount == 0:
                    self._conn.rollback()
                    return False
                inserted = self._insert_semantic(row, tokens=tokens)
                self._conn.execute(
                    "INSERT INTO semantic_fts(rowid, statement_tokens) VALUES (?, ?)",
                    (inserted, tokens),
                )
                self._conn.commit()
                return True
        except sqlite3.Error:
            self._conn.rollback()
            logger.exception("supersede_and_add_semantic failed old=%s new=%s", old_id, row.id)
            raise

    def _insert_semantic(self, row: SemanticRow, *, tokens: str) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO semantic
                (id, statement, statement_tokens, importance, pinned, source, speaker_origin,
                 memory_class, fact_key, supersedes_id, valid_from, valid_until,
                 provenance, deleted_at, owner_user_id, persona_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id,
                row.statement,
                tokens,
                row.importance,
                1 if row.pinned else 0,
                row.source,
                row.speaker_origin,
                row.memory_class,
                row.fact_key,
                row.supersedes_id,
                serialize_ts(row.valid_from),
                serialize_ts(row.valid_until),
                json.dumps(row.provenance, ensure_ascii=False),
                serialize_ts(row.deleted_at),
                row.owner_user_id,
                row.persona_id,
                serialize_ts(row.created_at),
                serialize_ts(row.updated_at),
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    def supersede_semantic(self, old_id: str, valid_until: datetime) -> bool:
        """把某条语义记忆标记为失效（被取代）。

        Returns:
            是否命中了一条活跃记录（命中并更新 → True）。
        """
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    UPDATE semantic SET valid_until = ?, updated_at = ?
                    WHERE id = ? AND valid_until IS NULL AND deleted_at IS NULL
                    """,
                    (serialize_ts(valid_until), serialize_ts(valid_until), old_id),
                )
                self._conn.commit()
                return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("supersede_semantic failed old_id=%s", old_id)
            raise

    def add_episodic(self, row: EpisodicRow) -> None:
        """插入一条情节记忆，同步写 FTS。"""
        tokens = _tokenize(row.summary)
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    INSERT INTO episodic
                        (id, summary, summary_tokens, source_ref, importance, participants,
                         occurred_at, deleted_at, owner_user_id, persona_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.id,
                        row.summary,
                        tokens,
                        row.source_ref,
                        row.importance,
                        json.dumps(row.participants, ensure_ascii=False),
                        serialize_ts(row.occurred_at),
                        serialize_ts(row.deleted_at),
                        row.owner_user_id,
                        row.persona_id,
                        serialize_ts(row.created_at),
                    ),
                )
                self._conn.execute(
                    "INSERT INTO episodic_fts(rowid, summary_tokens) VALUES (?, ?)",
                    (cur.lastrowid, tokens),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.exception("add_episodic failed id=%s owner=%s", row.id, row.owner_user_id)
            raise

    def soft_delete_by_source_events(
        self,
        *,
        session_id: str,
        event_uuids: set[str],
        deleted_at: datetime,
    ) -> dict[str, int]:
        """软删除来源命中指定 session event 的记忆。

        情节记忆按 ``source_ref`` 的首尾事件 uuid 匹配；Core / Semantic 同时
        识别直接 fragment source refs 与关联 episodic ids，避免没有 episodic
        摘要时丢失分支撤回能力。
        """
        if not session_id or not event_uuids:
            return {"episodic": 0, "semantic": 0, "core": 0}

        deleted_at_s = serialize_ts(deleted_at)
        try:
            with self._lock:
                episodic_rows = self._conn.execute(
                    """
                    SELECT id, source_ref FROM episodic
                    WHERE deleted_at IS NULL AND source_ref LIKE ?
                    """,
                    (f"{session_id}#%",),
                ).fetchall()
                episodic_ids = [
                    str(row["id"])
                    for row in episodic_rows
                    if _source_ref_touches(str(row["source_ref"]), session_id, event_uuids)
                ]

                episodic_count = 0
                if episodic_ids:
                    placeholders = ",".join("?" for _ in episodic_ids)
                    cur = self._conn.execute(
                        f"""
                        UPDATE episodic SET deleted_at = ?
                        WHERE deleted_at IS NULL AND id IN ({placeholders})
                        """,
                        [deleted_at_s, *episodic_ids],
                    )
                    episodic_count = cur.rowcount

                semantic_count = 0
                core_count = 0
                episodic_id_set = set(episodic_ids)
                semantic_rows = self._conn.execute(
                    """
                    SELECT id, provenance FROM semantic
                    WHERE deleted_at IS NULL AND valid_until IS NULL
                    """
                ).fetchall()
                semantic_ids: list[str] = []
                for row in semantic_rows:
                    try:
                        provenance = json.loads(row["provenance"])
                    except (TypeError, json.JSONDecodeError):
                        provenance = []
                    if isinstance(provenance, list) and any(
                        isinstance(item, str)
                        and (
                            item in episodic_id_set
                            or _source_ref_touches(item, session_id, event_uuids)
                        )
                        for item in provenance
                    ):
                        semantic_ids.append(str(row["id"]))

                if semantic_ids:
                    placeholders = ",".join("?" for _ in semantic_ids)
                    cur = self._conn.execute(
                        f"""
                        UPDATE semantic SET deleted_at = ?, updated_at = ?
                        WHERE deleted_at IS NULL AND id IN ({placeholders})
                        """,
                        [deleted_at_s, deleted_at_s, *semantic_ids],
                    )
                    semantic_count = cur.rowcount

                core_rows = self._conn.execute(
                    """SELECT id, provenance FROM core_profile_entry
                       WHERE deleted_at IS NULL AND valid_until IS NULL"""
                ).fetchall()
                core_ids: list[str] = []
                for row in core_rows:
                    try:
                        provenance = json.loads(row["provenance"])
                    except (TypeError, json.JSONDecodeError):
                        provenance = []
                    if isinstance(provenance, list) and any(
                        isinstance(item, str)
                        and (
                            item in episodic_id_set
                            or _source_ref_touches(item, session_id, event_uuids)
                        )
                        for item in provenance
                    ):
                        core_ids.append(str(row["id"]))
                if core_ids:
                    placeholders = ",".join("?" for _ in core_ids)
                    cur = self._conn.execute(
                        f"""UPDATE core_profile_entry SET deleted_at = ?, updated_at = ?
                            WHERE deleted_at IS NULL AND id IN ({placeholders})""",
                        [deleted_at_s, deleted_at_s, *core_ids],
                    )
                    core_count = cur.rowcount

                self._conn.commit()
                return {
                    "episodic": episodic_count,
                    "semantic": semantic_count,
                    "core": core_count,
                }
        except sqlite3.Error:
            logger.exception(
                "soft_delete_by_source_events failed session=%s events=%d",
                session_id,
                len(event_uuids),
            )
            raise

    # ----- 读 -----

    def list_core_profile_entries(
        self,
        *,
        owner_user_id: str,
        persona_id: str | None,
        include_history: bool = False,
    ) -> list[CoreProfileEntry]:
        """List user-global and current-persona Core Profile rows."""
        active = "" if include_history else " AND valid_until IS NULL AND deleted_at IS NULL"
        scope = (
            " AND (scope_kind = 'user' OR (scope_kind = 'persona' AND persona_id = ?))"
            if persona_id is not None
            else ""
        )
        params: tuple[object, ...] = (
            (owner_user_id, persona_id) if persona_id is not None else (owner_user_id,)
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM core_profile_entry
                WHERE owner_user_id = ?
                  {scope}
                  {active}
                ORDER BY field_key, normalized_value, id
                """,
                params,
            ).fetchall()
        return [_to_core_profile_entry(row) for row in rows]

    def core_profile(self, *, owner_user_id: str, persona_id: str) -> CoreProfile:
        return build_core_profile(
            self.list_core_profile_entries(owner_user_id=owner_user_id, persona_id=persona_id),
            owner_user_id=owner_user_id,
            persona_id=persona_id,
        )

    def core_profile_entries_by_ids(self, ids: list[str]) -> dict[str, CoreProfileEntry]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM core_profile_entry WHERE id IN ({placeholders})", ids
            ).fetchall()
        return {str(row["id"]): _to_core_profile_entry(row) for row in rows}

    def pinned(self, *, owner_user_id: str, persona_id: str, limit: int = 50) -> list[SemanticRow]:
        """取所有 pinned 的活跃语义记忆（按 importance 倒序）。

        pinned 是 user 维度事实，**跨 persona 共享**：只按 ``owner_user_id`` 过滤，
        不按 ``persona_id``（design §4.2）。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE pinned = 1 AND deleted_at IS NULL AND valid_until IS NULL
                  AND owner_user_id = ?
                ORDER BY importance DESC
                LIMIT ?
                """,
                (owner_user_id, limit),
            ).fetchall()
        return [_to_semantic(r) for r in rows]

    def core(self, *, owner_user_id: str, limit: int = 50) -> list[SemanticRow]:
        """取活跃 core 事实；core 是 owner 维度，跨 persona 共享。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE memory_class = 'core' AND deleted_at IS NULL AND valid_until IS NULL
                  AND owner_user_id = ?
                ORDER BY importance DESC, updated_at DESC
                LIMIT ?
                """,
                (owner_user_id, limit),
            ).fetchall()
        return [_to_semantic(row) for row in rows]

    def semantic_by_fact_key(
        self,
        fact_key: str,
        *,
        owner_user_id: str,
    ) -> SemanticRow | None:
        """返回某受限 fact key 的当前有效版本。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE fact_key = ? AND owner_user_id = ?
                  AND deleted_at IS NULL AND valid_until IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (fact_key, owner_user_id),
            ).fetchone()
        return _to_semantic(row) if row is not None else None

    def search_semantic(
        self,
        query: str,
        *,
        owner_user_id: str,
        persona_id: str,
        limit: int,
        include_historical: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[tuple[SemanticRow, float]]:
        """按关键词检索活跃语义记忆。

        Returns:
            ``(row, bm25_raw)`` 列表，按相关度从高到低（bm25 越小越相关）。
            语义跨 persona 共享，仅按 ``owner_user_id`` 过滤。
        """
        match = _fts_query(query)
        if match is None:
            return []
        sql = """
            SELECT s.*, bm25(semantic_fts) AS _bm25
            FROM semantic_fts
            JOIN semantic s ON s.rowid = semantic_fts.rowid
            WHERE semantic_fts MATCH ?
              AND s.deleted_at IS NULL AND s.owner_user_id = ?
        """
        params: list[Any] = [match, owner_user_id]
        if include_historical:
            if since is not None:
                sql += " AND (s.valid_until IS NULL OR s.valid_until > ?)"
                params.append(serialize_ts(since))
            if until is not None:
                sql += " AND COALESCE(s.valid_from, s.created_at) < ?"
                params.append(serialize_ts(until))
        else:
            sql += " AND s.valid_until IS NULL"
        sql += " ORDER BY _bm25 LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [(_to_semantic(r), float(r["_bm25"])) for r in rows]

    def semantic_by_ids(self, ids: list[str]) -> dict[str, SemanticRow]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM semantic WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {str(row["id"]): _to_semantic(row) for row in rows}

    def episodic_by_ids(self, ids: list[str]) -> dict[str, EpisodicRow]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM episodic WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {str(row["id"]): _to_episodic(row) for row in rows}

    def search_episodic(
        self, query: str, *, owner_user_id: str, persona_id: str | None, limit: int
    ) -> list[tuple[EpisodicRow, float]]:
        """按关键词检索活跃情节记忆。episodic 按 ``persona_id`` 隔离；

        ``persona_id=None`` 表示不过滤 persona，跨 persona 搜索。
        """
        match = _fts_query(query)
        if match is None:
            return []
        with self._lock:
            if persona_id is None:
                rows = self._conn.execute(
                    """
                    SELECT e.*, bm25(episodic_fts) AS _bm25
                    FROM episodic_fts
                    JOIN episodic e ON e.rowid = episodic_fts.rowid
                    WHERE episodic_fts MATCH ?
                      AND e.deleted_at IS NULL
                      AND e.owner_user_id = ?
                    ORDER BY _bm25
                    LIMIT ?
                    """,
                    (match, owner_user_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT e.*, bm25(episodic_fts) AS _bm25
                    FROM episodic_fts
                    JOIN episodic e ON e.rowid = episodic_fts.rowid
                    WHERE episodic_fts MATCH ?
                      AND e.deleted_at IS NULL
                      AND e.owner_user_id = ? AND e.persona_id = ?
                    ORDER BY _bm25
                    LIMIT ?
                    """,
                    (match, owner_user_id, persona_id, limit),
                ).fetchall()
        return [(_to_episodic(r), float(r["_bm25"])) for r in rows]

    def fts_match_pinned(self, query: str, *, owner_user_id: str) -> set[str]:
        """返回 ``query`` 命中（jieba 切词 + FTS5 unicode61）的活跃 pinned 条目 id 集合。

        为 [`013 pass-1 §5`](../../../docs/requirements/013-memory-quality-pass-1/design.md)
        的 pinned relevance gate 服务：retrieve 时用本接口判断 query 是否真的"问到了"
        pinned 里的事，从而决定是否注入 pinned，避免 query 与 pinned 无关时 pinned
        作为占位安慰品挤掉 episodic/semantic（issue 003 主因 2）。

        pinned 是 user 维度（跨 persona 共享），与 :meth:`pinned` 一致只按
        ``owner_user_id`` 过滤；空 query / 切词后为空返回空集。
        """
        match = _fts_query(query)
        if match is None:
            return set()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id
                FROM semantic_fts
                JOIN semantic s ON s.rowid = semantic_fts.rowid
                WHERE semantic_fts MATCH ?
                  AND s.pinned = 1 AND s.deleted_at IS NULL AND s.valid_until IS NULL
                  AND s.owner_user_id = ?
                """,
                (match, owner_user_id),
            ).fetchall()
        return {str(r["id"]) for r in rows}

    def related_semantic(
        self, *, owner_user_id: str, persona_id: str, limit: int = _RELATED_DEFAULT_LIMIT
    ) -> list[SemanticRow]:
        """供 reconcile：取该 scope 下的活跃语义事实，喂给 LLM 判断 add/supersede。

        v1 兜底返回全部活跃事实（孵化期量小）；超过 ``limit`` 按 ``updated_at``
        最近优先截断。未来事实变多时可改为按 fragment 关键词召回（不动调用方）。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE deleted_at IS NULL AND valid_until IS NULL
                  AND owner_user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (owner_user_id, limit),
            ).fetchall()
        return [_to_semantic(r) for r in rows]

    def list_active_ordinary_semantic(self, *, owner_user_id: str) -> list[SemanticRow]:
        """Return every active ordinary Semantic, newest first, without a count cap."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE deleted_at IS NULL AND valid_until IS NULL
                  AND owner_user_id = ? AND memory_class = 'normal' AND pinned = 0
                ORDER BY updated_at DESC, id ASC
                """,
                (owner_user_id,),
            ).fetchall()
        return [_to_semantic(row) for row in rows]

    def list_semantic(
        self, *, owner_user_id: str, limit: int = 50, offset: int = 0
    ) -> list[SemanticRow]:
        """按 created_at DESC 列活跃语义记忆（跨 persona，按 owner 过滤）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM semantic
                WHERE deleted_at IS NULL AND valid_until IS NULL
                  AND owner_user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (owner_user_id, limit, offset),
            ).fetchall()
        return [_to_semantic(r) for r in rows]

    def list_episodic(
        self,
        *,
        owner_user_id: str,
        persona_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EpisodicRow]:
        """按 occurred_at DESC 列活跃情节记忆。``persona_id=None`` 时不加 persona 过滤。"""
        base = """
            SELECT * FROM episodic
            WHERE deleted_at IS NULL AND owner_user_id = ?
        """
        params: list[Any] = [owner_user_id]
        if persona_id is not None:
            base += " AND persona_id = ?"
            params.append(persona_id)
        base += " ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(base, params).fetchall()
        return [_to_episodic(r) for r in rows]

    # ----- dense derived-index manifest -----

    def upsert_embedding_generation(
        self,
        *,
        generation_id: str,
        model_id: str,
        dimension: int,
        status: str,
        total_count: int,
        indexed_count: int,
        now: datetime,
        last_error: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO embedding_generation(
                    generation_id, model_id, dimension, status, total_count,
                    indexed_count, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(generation_id) DO UPDATE SET
                    status=excluded.status,
                    total_count=excluded.total_count,
                    indexed_count=excluded.indexed_count,
                    updated_at=excluded.updated_at,
                    last_error=excluded.last_error
                """,
                (
                    generation_id,
                    model_id,
                    dimension,
                    status,
                    total_count,
                    indexed_count,
                    serialize_ts(now),
                    serialize_ts(now),
                    last_error,
                ),
            )
            self._conn.commit()

    def embedding_generation(self, generation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM embedding_generation WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def active_embedding_generation(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'active_embedding_generation'"
            ).fetchone()
        return str(row["value"]) if row is not None and row["value"] else None

    def set_active_embedding_generation(self, generation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO schema_meta(key, value) VALUES ('active_embedding_generation', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (generation_id,),
            )
            self._conn.commit()

    def embedding_manifest(self, generation_id: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT evidence_id, content_hash FROM embedding_manifest
                WHERE generation_id = ?
                """,
                (generation_id,),
            ).fetchall()
        return {str(row["evidence_id"]): str(row["content_hash"]) for row in rows}

    def upsert_embedding_manifest(
        self,
        generation_id: str,
        entries: list[tuple[str, str]],
        *,
        indexed_at: datetime,
    ) -> None:
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO embedding_manifest(
                    generation_id, evidence_id, content_hash, indexed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(generation_id, evidence_id) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    indexed_at=excluded.indexed_at
                """,
                [
                    (generation_id, evidence_id, content_hash, serialize_ts(indexed_at))
                    for evidence_id, content_hash in entries
                ],
            )
            self._conn.commit()

    def delete_embedding_manifest(self, generation_id: str, evidence_ids: list[str]) -> None:
        if not evidence_ids:
            return
        placeholders = ",".join("?" for _ in evidence_ids)
        with self._lock:
            self._conn.execute(
                f"""
                DELETE FROM embedding_manifest
                WHERE generation_id = ? AND evidence_id IN ({placeholders})
                """,
                [generation_id, *evidence_ids],
            )
            self._conn.commit()

    def delete_embedding_generation(self, generation_id: str) -> None:
        """删除已退役 generation 的 manifest 元数据。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM embedding_generation WHERE generation_id = ?",
                (generation_id,),
            )
            self._conn.commit()

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            self._conn.close()


def _source_ref_touches(source_ref: str, session_id: str, event_uuids: set[str]) -> bool:
    """判断 ``source_ref`` 的事件端点是否命中给定 uuid 集合。"""
    prefix = f"{session_id}#"
    if not source_ref.startswith(prefix):
        return False
    tail = source_ref[len(prefix) :]
    if not tail:
        return False
    start, sep, end = tail.partition("..")
    return start in event_uuids or (bool(sep) and end in event_uuids)


# ----- 行映射 -----


def _to_semantic(r: sqlite3.Row) -> SemanticRow:
    return SemanticRow(
        id=r["id"],
        statement=r["statement"],
        persona_id=r["persona_id"],
        created_at=parse_ts(r["created_at"]),  # type: ignore[arg-type]
        updated_at=parse_ts(r["updated_at"]),  # type: ignore[arg-type]
        importance=r["importance"],
        pinned=bool(r["pinned"]),
        source=r["source"],
        speaker_origin=r["speaker_origin"],
        valid_from=parse_ts(r["valid_from"]),
        valid_until=parse_ts(r["valid_until"]),
        provenance=json.loads(r["provenance"]),
        deleted_at=parse_ts(r["deleted_at"]),
        owner_user_id=r["owner_user_id"],
        memory_class=r["memory_class"],
        fact_key=r["fact_key"],
        supersedes_id=r["supersedes_id"],
    )


def _to_episodic(r: sqlite3.Row) -> EpisodicRow:
    return EpisodicRow(
        id=r["id"],
        summary=r["summary"],
        source_ref=r["source_ref"],
        persona_id=r["persona_id"],
        occurred_at=parse_ts(r["occurred_at"]),  # type: ignore[arg-type]
        created_at=parse_ts(r["created_at"]),  # type: ignore[arg-type]
        importance=r["importance"],
        participants=json.loads(r["participants"]),
        deleted_at=parse_ts(r["deleted_at"]),
        owner_user_id=r["owner_user_id"],
    )


def _to_core_profile_entry(r: sqlite3.Row) -> CoreProfileEntry:
    value = json.loads(r["value_json"])
    if not isinstance(value, str):
        raise ValueError(f"core profile value must be a string: {r['id']}")
    valid_from = parse_ts(r["valid_from"])
    created_at = parse_ts(r["created_at"])
    updated_at = parse_ts(r["updated_at"])
    if valid_from is None or created_at is None or updated_at is None:
        raise ValueError(f"core profile timestamps are required: {r['id']}")
    return CoreProfileEntry(
        id=str(r["id"]),
        owner_user_id=str(r["owner_user_id"]),
        scope_kind=r["scope_kind"],
        persona_id=str(r["persona_id"]),
        field_key=r["field_key"],
        value=value,
        normalized_value=str(r["normalized_value"]),
        valid_from=valid_from,
        valid_until=parse_ts(r["valid_until"]),
        supersedes_id=r["supersedes_id"],
        provenance=tuple(json.loads(r["provenance"])),
        created_at=created_at,
        updated_at=updated_at,
        deleted_at=parse_ts(r["deleted_at"]),
    )


def _insert_core_profile_entry(conn: sqlite3.Connection, row: CoreProfileEntry) -> None:
    spec = CORE_FIELD_SPECS[row.field_key]
    expected_persona = row.persona_id if spec.scope_kind == "persona" else ""
    if (
        row.scope_kind != spec.scope_kind
        or row.persona_id != expected_persona
        or (spec.scope_kind == "persona" and not row.persona_id)
    ):
        raise ValueError(f"invalid scope for {row.field_key}")
    value = validate_core_value(row.field_key, row.value)
    if row.value != value or row.normalized_value != normalize_core_value(value):
        raise ValueError(f"invalid normalized value for {row.field_key}")
    conn.execute(
        """
        INSERT INTO core_profile_entry(
            id, owner_user_id, scope_kind, persona_id, field_key, value_json,
            normalized_value, valid_from, valid_until, supersedes_id, provenance,
            created_at, updated_at, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.id,
            row.owner_user_id,
            row.scope_kind,
            row.persona_id,
            row.field_key,
            json.dumps(row.value, ensure_ascii=False),
            row.normalized_value,
            serialize_ts(row.valid_from),
            serialize_ts(row.valid_until),
            row.supersedes_id,
            json.dumps(list(row.provenance), ensure_ascii=False),
            serialize_ts(row.created_at),
            serialize_ts(row.updated_at),
            serialize_ts(row.deleted_at),
        ),
    )


_MAX_QUERY_TOKENS = 64
"""单次 MATCH 拼接的 query token 上限，防超长 query 生成过多子句。"""


def _tokenize(text: str) -> str:
    """jieba 切词后空格连接，作为 FTS5 unicode61 索引文本。

    标点 / 空白 token 不显式过滤——FTS5 unicode61 tokenizer 会按 unicode word
    边界切分并丢掉标点。空字符串 / 全空白输入返回空串。详见
    docs/requirements/013-memory-quality-pass-1/design.md §4。
    """
    if not text or not text.strip():
        return ""
    return " ".join(jieba.cut(text))


def _fts_query(text: str) -> str | None:
    """把 query 转成 FTS5 MATCH 串：jieba 切词后用 OR 连接。

    Pass-1（013）：与原 trigram 滑窗实现的根本差异——每个 jieba 词成为一个 FTS5
    query token，跟 unicode61 索引按词匹配；中文短 query "宠物" / 长 query "你还记得
    我家里有什么宠物吗" 都能命中包含 "宠物" 词的记忆条目。详见 design.md §4。

    Returns:
        OR 连接的 token MATCH 串；query 切词后为空（如纯标点 / 空字符串）返回 ``None``
        让调用方跳过检索。
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in jieba.cut(text):
        tok = tok.strip()
        if not tok or tok in seen:
            continue
        # FTS5 unicode61 会把纯标点 token 视作无索引内容；query 侧也跳过避免
        # 'MATCH ".,"' 这种空 phrase 报错
        if not any(ch.isalnum() for ch in tok):
            continue
        seen.add(tok)
        tokens.append(tok)
        if len(tokens) >= _MAX_QUERY_TOKENS:
            break
    if not tokens:
        return None
    return " OR ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


# ===== schema migration =====


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if row is None:
        return 0
    meta = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(meta["value"]) if meta else 1


def _premigrate_schema(conn: sqlite3.Connection) -> bool:
    """检测旧库（schema v1）并做 in-place 结构升级。

    在 :data:`DDL_STATEMENTS` 跑之前调用——必须先 ALTER TABLE 加 ``*_tokens`` 列
    + DROP 旧 trigram FTS5 表，然后 DDL 的 `CREATE IF NOT EXISTS` 才会用新的
    unicode61 配置建出新 FTS5。否则旧 FTS5 表存在、`IF NOT EXISTS` 跳过，
    永远卡在 trigram。

    Returns:
        ``True`` 表示做了迁移、调用方需要回填 ``*_tokens`` + 重建 FTS5 索引；
        ``False`` 表示新库 / 已是 v2，无需后续 backfill。
    """
    # 全新库：schema_meta 表都不存在，无需迁移
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if row is None:
        return False

    # 版本检查
    meta = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    current = int(meta["value"]) if meta else 1
    if current >= SCHEMA_VERSION:
        return False

    need_token_backfill = current < 2
    if need_token_backfill:
        if _column_missing(conn, "semantic", "statement_tokens"):
            conn.execute(
                "ALTER TABLE semantic ADD COLUMN statement_tokens TEXT NOT NULL DEFAULT ''"
            )
        if _column_missing(conn, "episodic", "summary_tokens"):
            conn.execute("ALTER TABLE episodic ADD COLUMN summary_tokens TEXT NOT NULL DEFAULT ''")
        conn.execute("DROP TABLE IF EXISTS semantic_fts")
        conn.execute("DROP TABLE IF EXISTS episodic_fts")

    if _column_missing(conn, "semantic", "memory_class"):
        conn.execute("ALTER TABLE semantic ADD COLUMN memory_class TEXT NOT NULL DEFAULT 'normal'")
    if _column_missing(conn, "semantic", "fact_key"):
        conn.execute("ALTER TABLE semantic ADD COLUMN fact_key TEXT")
    if _column_missing(conn, "semantic", "supersedes_id"):
        conn.execute("ALTER TABLE semantic ADD COLUMN supersedes_id TEXT")
    _migrate_legacy_pinned(conn)
    return need_token_backfill


def _column_missing(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column not in cols


def _backfill_tokens_and_rebuild_fts(conn: sqlite3.Connection) -> None:
    """v1 → v2 迁移的数据补齐：jieba 切词回填 ``*_tokens`` 列 + 灌入新 FTS5 索引。

    在 :func:`_premigrate_v1_to_v2` 返回 ``True``、且 DDL 已重建新 FTS5 表之后调用。
    """
    # semantic
    rows = conn.execute("SELECT rowid, statement FROM semantic").fetchall()
    for r in rows:
        tokens = _tokenize(r["statement"])
        conn.execute(
            "UPDATE semantic SET statement_tokens = ? WHERE rowid = ?",
            (tokens, r["rowid"]),
        )
        conn.execute(
            "INSERT INTO semantic_fts(rowid, statement_tokens) VALUES (?, ?)",
            (r["rowid"], tokens),
        )
    # episodic
    rows = conn.execute("SELECT rowid, summary FROM episodic").fetchall()
    for r in rows:
        tokens = _tokenize(r["summary"])
        conn.execute(
            "UPDATE episodic SET summary_tokens = ? WHERE rowid = ?",
            (tokens, r["rowid"]),
        )
        conn.execute(
            "INSERT INTO episodic_fts(rowid, summary_tokens) VALUES (?, ?)",
            (r["rowid"], tokens),
        )


_USER_NAME_RE = re.compile(r"用户(?:的)?(?:名字|姓名)(?:是|叫)|用户叫")
_AGENT_NAME_RE = re.compile(r"(?:AI|助手|agent)(?:的)?(?:名字|姓名)?(?:是|叫)", re.IGNORECASE)
_CORE_RELATION_RE = re.compile(
    r"用户(?:的)?(?:伴侣|妻子|丈夫|男朋友|女朋友|父亲|母亲|爸爸|妈妈|儿子|女儿|孩子)"
)


def _migrate_legacy_pinned(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, statement, pinned FROM semantic").fetchall()
    for row in rows:
        statement = str(row["statement"])
        if not bool(row["pinned"]):
            memory_class, fact_key = "normal", None
        elif _USER_NAME_RE.search(statement):
            memory_class, fact_key = "core", "user.name"
        elif _AGENT_NAME_RE.search(statement):
            memory_class, fact_key = "core", "agent.name"
        elif _CORE_RELATION_RE.search(statement):
            memory_class, fact_key = "core", None
        else:
            memory_class, fact_key = "priority", None
        conn.execute(
            "UPDATE semantic SET memory_class = ?, fact_key = COALESCE(fact_key, ?) WHERE id = ?",
            (memory_class, fact_key, row["id"]),
        )


_V5_VALUE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "user.name": (
        re.compile(r"^用户(?:的)?(?:名字|姓名)(?:是|叫|为)\s*(.+)$"),
        re.compile(r"^用户(?:叫|名叫)\s*(.+)$"),
    ),
    "agent.name": (
        re.compile(r"^(?:AI|助手|智能体|agent)(?:的)?(?:名字|名称)(?:是|叫|为)\s*(.+)$", re.I),
        re.compile(r"^(?:AI|助手|智能体|agent)(?:叫|名叫)\s*(.+)$", re.I),
    ),
    "user.residence": (re.compile(r"^用户(?:目前|现在)?(?:住在|居住在|定居在|家住)\s*(.+)$"),),
}


def _migrate_core_profile_v5(conn: sqlite3.Connection) -> None:
    """Conservatively project unambiguous v4 fact keys into Core Profile."""
    rows = conn.execute(
        """
        SELECT * FROM semantic
        WHERE deleted_at IS NULL AND valid_until IS NULL
        ORDER BY updated_at DESC, id ASC
        """
    ).fetchall()
    candidates: dict[
        tuple[str, CoreScopeKind, str, CoreFieldKey], list[tuple[sqlite3.Row, str]]
    ] = {}
    key_map: dict[str, CoreFieldKey] = {
        "user.name": "user.preferred_name",
        "user.residence": "user.current_residences",
        "agent.name": "relationship.agent_nickname",
    }
    for row in rows:
        old_key = row["fact_key"]
        field_key = key_map.get(old_key)
        if field_key is None:
            continue
        value = _extract_v5_value(str(row["statement"]), old_key)
        if value is None:
            continue
        spec = CORE_FIELD_SPECS[field_key]
        persona_id = str(row["persona_id"]) if spec.scope_kind == "persona" else ""
        if spec.scope_kind == "persona" and not persona_id:
            continue
        group = (str(row["owner_user_id"]), spec.scope_kind, persona_id, field_key)
        candidates.setdefault(group, []).append((row, value))

    now = datetime.now(UTC)
    migrated_ids: set[str] = set()
    for (owner, scope_kind, persona_id, field_key), grouped in candidates.items():
        spec = CORE_FIELD_SPECS[field_key]
        if spec.cardinality == "single" and len(grouped) != 1:
            logger.warning(
                "core migration kept ambiguous singleton as ordinary field=%s count=%d",
                field_key,
                len(grouped),
            )
            continue
        seen_values: set[str] = set()
        for row, value in grouped:
            normalized = normalize_core_value(value)
            if normalized in seen_values:
                migrated_ids.add(str(row["id"]))
                continue
            seen_values.add(normalized)
            created_at = parse_ts(row["created_at"]) or now
            updated_at = parse_ts(row["updated_at"]) or created_at
            valid_from = parse_ts(row["valid_from"]) or created_at
            _insert_core_profile_entry(
                conn,
                CoreProfileEntry(
                    id=str(uuid4()),
                    owner_user_id=owner,
                    scope_kind=scope_kind,
                    persona_id=persona_id,
                    field_key=field_key,
                    value=value,
                    normalized_value=normalized,
                    valid_from=valid_from,
                    valid_until=None,
                    supersedes_id=None,
                    provenance=tuple(json.loads(row["provenance"])),
                    created_at=created_at,
                    updated_at=updated_at,
                ),
            )
            migrated_ids.add(str(row["id"]))

    if migrated_ids:
        placeholders = ",".join("?" for _ in migrated_ids)
        conn.execute(
            f"""
            UPDATE semantic SET valid_until = ?, updated_at = ?
            WHERE id IN ({placeholders}) AND valid_until IS NULL AND deleted_at IS NULL
            """,
            [serialize_ts(now), serialize_ts(now), *sorted(migrated_ids)],
        )
    conn.execute(
        """
        UPDATE semantic SET pinned = 0, memory_class = 'normal', fact_key = NULL
        WHERE deleted_at IS NULL AND valid_until IS NULL
        """
    )


def _extract_v5_value(statement: str, old_key: str) -> str | None:
    for pattern in _V5_VALUE_PATTERNS.get(old_key, ()):
        match = pattern.fullmatch(statement.strip())
        if match is None:
            continue
        value = match.group(1).strip().rstrip("。.!！")
        if old_key == "user.residence" and re.search(r"[、,，/]|(?:和|及)", value):
            return None
        value_key_map: dict[str, CoreFieldKey] = {
            "user.name": "user.preferred_name",
            "user.residence": "user.current_residences",
            "agent.name": "relationship.agent_nickname",
        }
        field_key = value_key_map[old_key]
        try:
            return validate_core_value(field_key, value)
        except ValueError:
            return None
    return None
