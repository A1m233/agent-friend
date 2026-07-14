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
from datetime import datetime
from pathlib import Path
from typing import Any

import jieba

from .schema import (
    DDL_STATEMENTS,
    SCHEMA_VERSION,
    EpisodicRow,
    SemanticRow,
    parse_ts,
    serialize_ts,
)

__all__ = ["SqliteMemoryStore"]

logger = logging.getLogger(__name__)

_RELATED_DEFAULT_LIMIT = 200
"""``related_semantic`` 兜底返回的活跃事实上限。

孵化期单 user 单 persona 的活跃事实远小于此；超出再谈分页 / 更精的召回。"""


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

    # ----- 写 -----

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

        情节记忆按 ``source_ref`` 的首尾事件 uuid 匹配；语义记忆按
        ``provenance`` 中被软删的 episodic id 匹配。语义层不排除 pinned，避免旧
        分支抽取出的高优先级事实继续召回。
        """
        if not session_id or not event_uuids:
            return {"episodic": 0, "semantic": 0}

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
                if episodic_ids:
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
                            isinstance(item, str) and item in episodic_id_set for item in provenance
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

                self._conn.commit()
                return {"episodic": episodic_count, "semantic": semantic_count}
        except sqlite3.Error:
            logger.exception(
                "soft_delete_by_source_events failed session=%s events=%d",
                session_id,
                len(event_uuids),
            )
            raise

    # ----- 读 -----

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
