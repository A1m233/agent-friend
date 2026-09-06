"""CPU ONNX embedding、sqlite-vec 派生索引与可恢复 generation 生命周期。"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .execution import check_cancel

if TYPE_CHECKING:
    from ..store import SqliteMemoryStore

QUERY_INSTRUCTION_ZH = "为这个句子生成表示以用于检索相关文章："
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorRecord:
    evidence_id: str
    vector: list[float]
    owner_user_id: str
    persona_id: str | None
    kind: str


@dataclass(frozen=True)
class VectorFilters:
    generation_id: str
    owner_user_id: str


@dataclass(frozen=True)
class VectorHit:
    evidence_id: str
    distance: float


class EmbeddingProvider(Protocol):
    model_id: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorIndex(Protocol):
    def upsert(self, generation_id: str, records: list[VectorRecord]) -> None: ...
    def search(
        self,
        vector: list[float],
        *,
        filters: VectorFilters,
        limit: int,
    ) -> list[VectorHit]: ...
    def delete(self, generation_id: str, evidence_ids: list[str]) -> None: ...
    def close(self) -> None: ...


class OnnxEmbeddingProvider:
    """惰性加载的 BGE ONNX CPU provider；构造不阻塞应用 ready。"""

    def __init__(
        self,
        model_path: Path,
        tokenizer_path: Path,
        *,
        model_id: str = "BAAI/bge-small-zh-v1.5",
        dimension: int = 512,
        batch_size: int = 32,
    ) -> None:
        self.model_id = model_id
        self.dimension = dimension
        self._model_path = model_path
        self._tokenizer_path = tokenizer_path
        self._batch_size = batch_size
        self._lock = threading.Lock()
        self._runtime: tuple[Any, Any, Any] | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        np, tokenizer, session = self._ensure_runtime()
        vectors: list[Any] = []
        input_names = {item.name for item in session.get_inputs()}
        for offset in range(0, len(texts), self._batch_size):
            batch = texts[offset : offset + self._batch_size]
            encodings = tokenizer.encode_batch(batch)
            feeds: dict[str, Any] = {
                "input_ids": np.asarray([item.ids for item in encodings], dtype=np.int64),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encodings], dtype=np.int64
                ),
            }
            if "token_type_ids" in input_names:
                feeds["token_type_ids"] = np.asarray(
                    [item.type_ids for item in encodings], dtype=np.int64
                )
            hidden = session.run(None, feeds)[0]
            pooled = hidden[:, 0, :]
            normalized = pooled / np.maximum(
                np.linalg.norm(pooled, axis=1, keepdims=True),
                1e-12,
            )
            vectors.extend(normalized.tolist())
        return [[float(value) for value in vector] for vector in vectors]

    def _ensure_runtime(self) -> tuple[Any, Any, Any]:
        with self._lock:
            if self._runtime is not None:
                return self._runtime
            if not self._model_path.exists() or not self._tokenizer_path.exists():
                raise RuntimeError("ONNX embedding assets are incomplete")
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self._tokenizer_path))
            tokenizer.enable_truncation(max_length=512)
            tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = max(1, min(os.cpu_count() or 1, 4))
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(self._model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._runtime = np, tokenizer, session
            return self._runtime


class SqliteVecIndex:
    """sqlite-vec 实现；普通 SQLite 表拥有稳定字符串 evidence id 映射。"""

    def __init__(self, path: Path, *, dimension: int) -> None:
        import sqlite_vec

        path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_vec = sqlite_vec
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_ids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generation_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                persona_id TEXT,
                kind TEXT NOT NULL,
                UNIQUE(generation_id, evidence_id)
            )
            """
        )
        self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vector_index USING vec0(
                id INTEGER PRIMARY KEY, embedding float[{dimension}]
            )
            """
        )
        self._conn.commit()

    def upsert(self, generation_id: str, records: list[VectorRecord]) -> None:
        with self._lock:
            for record in records:
                existing = self._conn.execute(
                    "SELECT id FROM vector_ids WHERE generation_id = ? AND evidence_id = ?",
                    (generation_id, record.evidence_id),
                ).fetchone()
                if existing is not None:
                    row_id = int(existing["id"])
                    self._conn.execute("DELETE FROM vector_index WHERE id = ?", (row_id,))
                    self._conn.execute("DELETE FROM vector_ids WHERE id = ?", (row_id,))
                cur = self._conn.execute(
                    """
                    INSERT INTO vector_ids(
                        generation_id, evidence_id, owner_user_id, persona_id, kind
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        generation_id,
                        record.evidence_id,
                        record.owner_user_id,
                        record.persona_id,
                        record.kind,
                    ),
                )
                assert cur.lastrowid is not None
                self._conn.execute(
                    "INSERT INTO vector_index(id, embedding) VALUES (?, ?)",
                    (
                        cur.lastrowid,
                        self._sqlite_vec.serialize_float32(record.vector),
                    ),
                )
            self._conn.commit()

    def search(
        self,
        vector: list[float],
        *,
        filters: VectorFilters,
        limit: int,
    ) -> list[VectorHit]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT mapping.evidence_id, hits.distance
                FROM (
                    SELECT id, distance FROM vector_index
                    WHERE embedding MATCH ? AND k = ? ORDER BY distance
                ) AS hits
                JOIN vector_ids AS mapping ON mapping.id = hits.id
                WHERE mapping.generation_id = ? AND mapping.owner_user_id = ?
                ORDER BY hits.distance
                LIMIT ?
                """,
                (
                    self._sqlite_vec.serialize_float32(vector),
                    max(limit * 4, limit),
                    filters.generation_id,
                    filters.owner_user_id,
                    limit,
                ),
            ).fetchall()
        return [
            VectorHit(evidence_id=str(row["evidence_id"]), distance=float(row["distance"]))
            for row in rows
        ]

    def delete(self, generation_id: str, evidence_ids: list[str]) -> None:
        with self._lock:
            for evidence_id in evidence_ids:
                row = self._conn.execute(
                    "SELECT id FROM vector_ids WHERE generation_id = ? AND evidence_id = ?",
                    (generation_id, evidence_id),
                ).fetchone()
                if row is not None:
                    self._conn.execute("DELETE FROM vector_index WHERE id = ?", (row["id"],))
                    self._conn.execute("DELETE FROM vector_ids WHERE id = ?", (row["id"],))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


@dataclass(frozen=True)
class DenseIndexState:
    status: str
    generation_id: str
    active_generation: str | None
    indexed_count: int
    total_count: int
    last_error: str | None = None


class EmbeddingIndexManager:
    """SQLite 正文到向量派生索引的可恢复后台回填管理器。"""

    def __init__(
        self,
        store: SqliteMemoryStore,
        provider: EmbeddingProvider,
        index: VectorIndex,
        *,
        owner_user_id: str = "local",
        batch_size: int = 64,
    ) -> None:
        self._store = store
        self._provider = provider
        self._index = index
        self._owner = owner_user_id
        self._batch_size = batch_size
        raw_generation = f"{provider.model_id}:{provider.dimension}"
        self.generation_id = hashlib.sha256(raw_generation.encode()).hexdigest()[:16]
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="memory-vector-backfill",
            daemon=True,
        )
        self._thread.start()
        self.request_backfill()

    def request_backfill(self) -> None:
        self._wake.set()

    def backfill_once(self) -> DenseIndexState:
        now = datetime.now(UTC)
        previous_active = self._store.active_embedding_generation()
        semantic = self._store.list_semantic(owner_user_id=self._owner, limit=100_000)
        episodic = self._store.list_episodic(
            owner_user_id=self._owner,
            persona_id=None,
            limit=100_000,
        )
        source: dict[str, tuple[str, str, str | None, str]] = {
            **{
                f"semantic:{row.id}": (
                    row.statement,
                    row.owner_user_id,
                    row.persona_id,
                    "semantic",
                )
                for row in semantic
            },
            **{
                f"episodic:{row.id}": (
                    row.summary,
                    row.owner_user_id,
                    row.persona_id,
                    "episodic",
                )
                for row in episodic
            },
        }
        manifest = self._store.embedding_manifest(self.generation_id)
        hashes = {
            evidence_id: _content_hash(self._provider.model_id, text)
            for evidence_id, (text, _, _, _) in source.items()
        }
        stale = [evidence_id for evidence_id in manifest if evidence_id not in source]
        if stale:
            self._index.delete(self.generation_id, stale)
            self._store.delete_embedding_manifest(self.generation_id, stale)
        pending = [
            evidence_id
            for evidence_id, content_hash in hashes.items()
            if manifest.get(evidence_id) != content_hash
        ]
        self._store.upsert_embedding_generation(
            generation_id=self.generation_id,
            model_id=self._provider.model_id,
            dimension=self._provider.dimension,
            status="building",
            total_count=len(source),
            indexed_count=len(source) - len(pending),
            now=now,
        )
        try:
            for offset in range(0, len(pending), self._batch_size):
                evidence_ids = pending[offset : offset + self._batch_size]
                vectors = self._provider.embed([source[item][0] for item in evidence_ids])
                records = [
                    VectorRecord(
                        evidence_id=evidence_id,
                        vector=vector,
                        owner_user_id=source[evidence_id][1],
                        persona_id=source[evidence_id][2],
                        kind=source[evidence_id][3],
                    )
                    for evidence_id, vector in zip(evidence_ids, vectors, strict=True)
                ]
                self._index.upsert(self.generation_id, records)
                self._store.upsert_embedding_manifest(
                    self.generation_id,
                    [(item, hashes[item]) for item in evidence_ids],
                    indexed_at=datetime.now(UTC),
                )
            self._store.upsert_embedding_generation(
                generation_id=self.generation_id,
                model_id=self._provider.model_id,
                dimension=self._provider.dimension,
                status="ready",
                total_count=len(source),
                indexed_count=len(source),
                now=datetime.now(UTC),
            )
            self._store.set_active_embedding_generation(self.generation_id)
            if previous_active is not None and previous_active != self.generation_id:
                self._retire_generation(previous_active)
        except Exception as exc:
            self._store.upsert_embedding_generation(
                generation_id=self.generation_id,
                model_id=self._provider.model_id,
                dimension=self._provider.dimension,
                status="failed",
                total_count=len(source),
                indexed_count=len(self._store.embedding_manifest(self.generation_id)),
                now=datetime.now(UTC),
                last_error=str(exc),
            )
        return self.state()

    def _retire_generation(self, generation_id: str) -> None:
        """新 generation 激活后尽力清理旧派生数据；失败不回滚新激活。"""
        try:
            evidence_ids = list(self._store.embedding_manifest(generation_id))
            self._index.delete(generation_id, evidence_ids)
            self._store.delete_embedding_generation(generation_id)
        except Exception:
            logger.warning("retire dense generation failed id=%s", generation_id, exc_info=True)

    def search(self, query: str, *, owner_user_id: str, limit: int) -> list[VectorHit]:
        return self.prepare_search(query, owner_user_id=owner_user_id)(limit)

    def prepare_search(
        self,
        query: str,
        *,
        owner_user_id: str,
        cancel_check: Callable[[], None] | None = None,
    ) -> Callable[[int], list[VectorHit]]:
        """Reuse one query embedding across this request's expanding limits."""
        vector: list[float] | None = None

        def search(limit: int) -> list[VectorHit]:
            nonlocal vector
            check_cancel(cancel_check)
            state = self.state()
            if state.status != "ready" or state.active_generation != self.generation_id:
                return []
            if vector is None:
                vector = self._provider.embed([QUERY_INSTRUCTION_ZH + query])[0]
            check_cancel(cancel_check)
            result = self._index.search(
                vector,
                filters=VectorFilters(
                    generation_id=self.generation_id,
                    owner_user_id=owner_user_id,
                ),
                limit=limit,
            )
            check_cancel(cancel_check)
            return result

        return search

    def state(self) -> DenseIndexState:
        row = self._store.embedding_generation(self.generation_id)
        if row is None:
            return DenseIndexState(
                status="keyword_ready",
                generation_id=self.generation_id,
                active_generation=self._store.active_embedding_generation(),
                indexed_count=0,
                total_count=0,
            )
        return DenseIndexState(
            status=str(row["status"]),
            generation_id=self.generation_id,
            active_generation=self._store.active_embedding_generation(),
            indexed_count=int(row["indexed_count"]),
            total_count=int(row["total_count"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._index.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if not self._stop.is_set():
                self.backfill_once()


def _content_hash(model_id: str, text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(f"{model_id}\n{normalized}".encode()).hexdigest()
