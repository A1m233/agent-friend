"""``SqliteMemoryStore``：episodic / semantic 的 SQLite 持久化 + FTS5 检索。

线程模型：异步抽取 worker 在后台线程写、对话主线程读。Python ``sqlite3``
连接对象本身不可跨线程并发使用，这里用 ``check_same_thread=False`` 开放跨线程，
并用一把 :class:`threading.RLock` 串行化所有访问（孵化期单进程，量小，足够）。
配合 WAL 模式让读写更顺。

详见 docs/requirements/008-engine-memory/design.md §4.3。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .schema import (
    DDL_STATEMENTS,
    SCHEMA_VERSION,
    EpisodicRow,
    SemanticRow,
    parse_ts,
    serialize_ts,
)

__all__ = ["SqliteMemoryStore"]

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
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            for ddl in DDL_STATEMENTS:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ----- 写 -----

    def add_semantic(self, row: SemanticRow) -> None:
        """插入一条语义记忆，同步写 FTS。"""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO semantic
                    (id, statement, importance, pinned, source, speaker_origin,
                     valid_from, valid_until, provenance, deleted_at,
                     owner_user_id, persona_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.id,
                    row.statement,
                    row.importance,
                    1 if row.pinned else 0,
                    row.source,
                    row.speaker_origin,
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
                "INSERT INTO semantic_fts(rowid, statement) VALUES (?, ?)",
                (cur.lastrowid, row.statement),
            )
            self._conn.commit()

    def supersede_semantic(self, old_id: str, valid_until: datetime) -> bool:
        """把某条语义记忆标记为失效（被取代）。

        Returns:
            是否命中了一条活跃记录（命中并更新 → True）。
        """
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

    def add_episodic(self, row: EpisodicRow) -> None:
        """插入一条情节记忆，同步写 FTS。"""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO episodic
                    (id, summary, source_ref, importance, participants,
                     occurred_at, deleted_at, owner_user_id, persona_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.id,
                    row.summary,
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
                "INSERT INTO episodic_fts(rowid, summary) VALUES (?, ?)",
                (cur.lastrowid, row.summary),
            )
            self._conn.commit()

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

    def search_semantic(
        self, query: str, *, owner_user_id: str, persona_id: str, limit: int
    ) -> list[tuple[SemanticRow, float]]:
        """按关键词检索活跃语义记忆。

        Returns:
            ``(row, bm25_raw)`` 列表，按相关度从高到低（bm25 越小越相关）。
            语义跨 persona 共享，仅按 ``owner_user_id`` 过滤。
        """
        match = _fts_query(query)
        if match is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*, bm25(semantic_fts) AS _bm25
                FROM semantic_fts
                JOIN semantic s ON s.rowid = semantic_fts.rowid
                WHERE semantic_fts MATCH ?
                  AND s.deleted_at IS NULL AND s.valid_until IS NULL
                  AND s.owner_user_id = ?
                ORDER BY _bm25
                LIMIT ?
                """,
                (match, owner_user_id, limit),
            ).fetchall()
        return [(_to_semantic(r), float(r["_bm25"])) for r in rows]

    def search_episodic(
        self, query: str, *, owner_user_id: str, persona_id: str, limit: int
    ) -> list[tuple[EpisodicRow, float]]:
        """按关键词检索活跃情节记忆。episodic 按 ``persona_id`` 隔离。"""
        match = _fts_query(query)
        if match is None:
            return []
        with self._lock:
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

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            self._conn.close()


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


_MAX_TRIGRAMS = 64
"""单次 MATCH 拼接的 3-gram 上限，防止超长 query 生成过多子句。"""


def _fts_query(text: str) -> str | None:
    """把自由文本转成 trigram FTS5 的 MATCH 串：query 的 3-gram 集合 OR 起来。

    为什么不整句作短语：trigram 下"短语"= 子串精确匹配，整句 query 几乎不可能
    是某条短事实的子串。改成"共享任一 3-gram 即召回"，让查询与记忆按 n-gram 重叠
    度模糊匹配（bm25 再按重叠多少排序），中英文都适用。

    Returns:
        OR 连接的 trigram MATCH 串；query 里凑不出任何 3-gram（如全是 1~2 字的
        碎片）时返回 ``None`` 让调用方跳过检索。
    """
    grams: list[str] = []
    seen: set[str] = set()
    for chunk in text.split():
        if len(chunk) < 3:
            continue
        for i in range(len(chunk) - 2):
            g = chunk[i : i + 3]
            if g not in seen:
                seen.add(g)
                grams.append(g)
                if len(grams) >= _MAX_TRIGRAMS:
                    break
        if len(grams) >= _MAX_TRIGRAMS:
            break
    if not grams:
        return None
    return " OR ".join(f'"{g.replace(chr(34), chr(34) * 2)}"' for g in grams)
