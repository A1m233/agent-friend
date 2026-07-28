"""M48.0 benchmark 编排与各候选底座 runner。"""

from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from agent.paths import sessions_dir
from agent.sessions import Event, JsonlSessionStore, Session
from agent.tools.builtin.conversation_history import ConversationHistoryTool
from memory.store import SemanticRow, SqliteMemoryStore

from .fixtures import BenchmarkDocument, BenchmarkQuery
from .metrics import evaluate_rankings, percentile, reciprocal_rank_fusion

_QUERY_INSTRUCTION_ZH = "为这个句子生成表示以用于检索相关文章："
_OWNER_USER_ID = "benchmark-user"
_PERSONA_ID = "benchmark-persona"


@dataclass(frozen=True)
class BenchmarkConfig:
    """一次 benchmark 的稳定输入。"""

    corpus_size: int
    top_k: int
    iterations: int
    model_id: str
    model_revision: str
    device: str
    batch_size: int
    history_source: str
    history_sessions: int
    history_messages_per_session: int


def run_keyword_benchmark(
    documents: Sequence[BenchmarkDocument],
    queries: Sequence[BenchmarkQuery],
    *,
    db_path: Path,
    top_k: int,
    iterations: int,
) -> tuple[dict[str, object], dict[str, list[str]]]:
    """用生产 ``SqliteMemoryStore`` 跑当前 FTS5 + jieba 基线。"""
    rss_before = _rss_mb()
    started = time.perf_counter()
    store = SqliteMemoryStore(db_path)
    now = datetime.now(UTC)
    for document in documents:
        store.add_semantic(
            SemanticRow(
                id=document.id,
                statement=document.text,
                persona_id=_PERSONA_ID,
                created_at=now,
                updated_at=now,
                owner_user_id=_OWNER_USER_ID,
            )
        )
    build_ms = _elapsed_ms(started)
    rss_after_build = _rss_mb()
    store.close()

    started = time.perf_counter()
    store = SqliteMemoryStore(db_path)
    store.warmup()
    startup_ms = _elapsed_ms(started)

    rankings = _keyword_rankings(store, queries, top_k)
    query_latencies: list[float] = []
    for _ in range(iterations):
        for query in queries:
            started = time.perf_counter()
            store.search_semantic(
                query.text,
                owner_user_id=_OWNER_USER_ID,
                persona_id=_PERSONA_ID,
                limit=top_k,
            )
            query_latencies.append(_elapsed_ms(started))
    store.close()

    result: dict[str, object] = {
        "status": "ok",
        "engine": "sqlite-fts5-jieba",
        "quality": evaluate_rankings(queries, rankings),
        "build_ms": round(build_ms, 3),
        "startup_ms": round(startup_ms, 3),
        "query_latency_ms": _latency_summary(query_latencies),
        "rss_before_mb": rss_before,
        "rss_after_build_mb": rss_after_build,
        "rss_delta_mb": _delta(rss_before, rss_after_build),
        "index_disk_mb": _bytes_to_mb(_tree_size(db_path)),
    }
    return result, rankings


def _keyword_rankings(
    store: SqliteMemoryStore, queries: Sequence[BenchmarkQuery], top_k: int
) -> dict[str, list[str]]:
    return {
        query.id: [
            row.id
            for row, _score in store.search_semantic(
                query.text,
                owner_user_id=_OWNER_USER_ID,
                persona_id=_PERSONA_ID,
                limit=top_k,
            )
        ]
        for query in queries
    }


def run_dense_benchmark(
    documents: Sequence[BenchmarkDocument],
    queries: Sequence[BenchmarkQuery],
    *,
    index_dir: Path,
    top_k: int,
    iterations: int,
    model_id: str,
    model_revision: str,
    device: str,
    batch_size: int,
) -> tuple[dict[str, object], dict[str, list[str]]]:
    """用 Sentence Transformers 预计算 embedding，再写入本地 Chroma。"""
    rss_before = _rss_mb()
    try:
        started = time.perf_counter()
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "缺少 M48.0 可选依赖；请从 scripts/memory-retrieval-benchmark/run.* 运行"
        ) from exc
    chroma_import_ms = _elapsed_ms(started)
    rss_after_chroma_import = _rss_mb()

    try:
        started = time.perf_counter()
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "缺少 M48.0 可选依赖；请从 scripts/memory-retrieval-benchmark/run.* 运行"
        ) from exc
    embedding_library_import_ms = _elapsed_ms(started)
    rss_after_embedding_library_import = _rss_mb()

    started = time.perf_counter()
    model = SentenceTransformer(model_id, revision=model_revision, device=device)
    model_load_ms = _elapsed_ms(started)
    rss_after_model = _rss_mb()

    texts = [document.text for document in documents]
    started = time.perf_counter()
    document_embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    document_encode_ms = _elapsed_ms(started)
    vectors = document_embeddings.tolist()
    dimension = len(vectors[0]) if vectors else 0

    started = time.perf_counter()
    client = chromadb.PersistentClient(path=str(index_dir))
    collection = client.create_collection(
        name="memory_retrieval_benchmark",
        metadata={"hnsw:space": "cosine"},
    )
    for offset in range(0, len(documents), batch_size):
        batch = documents[offset : offset + batch_size]
        collection.add(
            ids=[document.id for document in batch],
            documents=[document.text for document in batch],
            embeddings=vectors[offset : offset + batch_size],
        )
    index_build_ms = _elapsed_ms(started)
    rss_after_index = _rss_mb()

    query_texts = [_QUERY_INSTRUCTION_ZH + query.text for query in queries]
    query_embeddings = model.encode(
        query_texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).tolist()
    rankings = _dense_rankings(collection, queries, query_embeddings, top_k)

    embedding_latencies: list[float] = []
    index_latencies: list[float] = []
    end_to_end_latencies: list[float] = []
    for _ in range(iterations):
        for query in queries:
            end_started = time.perf_counter()
            started = time.perf_counter()
            query_vector = model.encode(
                [_QUERY_INSTRUCTION_ZH + query.text],
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            ).tolist()
            embedding_latencies.append(_elapsed_ms(started))
            started = time.perf_counter()
            collection.query(query_embeddings=query_vector, n_results=min(top_k, len(documents)))
            index_latencies.append(_elapsed_ms(started))
            end_to_end_latencies.append(_elapsed_ms(end_started))

    _close_chroma_client(client)
    del collection
    del client
    gc.collect()
    started = time.perf_counter()
    reopened_client = chromadb.PersistentClient(path=str(index_dir))
    reopened_collection = reopened_client.get_collection("memory_retrieval_benchmark")
    startup_ms = _elapsed_ms(started)
    del reopened_collection
    _close_chroma_client(reopened_client)
    del reopened_client
    gc.collect()

    result: dict[str, object] = {
        "status": "ok",
        "engine": "chromadb",
        "engine_version": _package_version("chromadb"),
        "embedding_library": "sentence-transformers",
        "embedding_library_version": _package_version("sentence-transformers"),
        "model_id": model_id,
        "requested_revision": model_revision,
        "resolved_revision": _resolved_model_revision(model, model_revision),
        "device": device,
        "dimension": dimension,
        "query_instruction": _QUERY_INSTRUCTION_ZH,
        "quality": evaluate_rankings(queries, rankings),
        "chroma_import_ms": round(chroma_import_ms, 3),
        "embedding_library_import_ms": round(embedding_library_import_ms, 3),
        "model_load_ms": round(model_load_ms, 3),
        "document_encode_ms": round(document_encode_ms, 3),
        "document_encode_per_item_ms": round(document_encode_ms / max(len(documents), 1), 6),
        "index_build_ms": round(index_build_ms, 3),
        "startup_ms": round(startup_ms, 3),
        "query_embedding_latency_ms": _latency_summary(embedding_latencies),
        "index_query_latency_ms": _latency_summary(index_latencies),
        "end_to_end_query_latency_ms": _latency_summary(end_to_end_latencies),
        "rss_before_mb": rss_before,
        "rss_after_chroma_import_mb": rss_after_chroma_import,
        "rss_after_embedding_library_import_mb": rss_after_embedding_library_import,
        "rss_after_model_mb": rss_after_model,
        "rss_after_index_mb": rss_after_index,
        "rss_chroma_import_delta_mb": _delta(rss_before, rss_after_chroma_import),
        "rss_embedding_library_import_delta_mb": _delta(
            rss_after_chroma_import, rss_after_embedding_library_import
        ),
        "rss_model_load_delta_mb": _delta(rss_after_embedding_library_import, rss_after_model),
        "rss_pre_index_total_delta_mb": _delta(rss_before, rss_after_model),
        "rss_total_delta_mb": _delta(rss_before, rss_after_index),
        "model_asset_mb": _model_asset_mb(model_id, model_revision),
        "index_disk_mb": _bytes_to_mb(_tree_size(index_dir)),
    }
    return result, rankings


def _close_chroma_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _dense_rankings(
    collection: Any,
    queries: Sequence[BenchmarkQuery],
    query_embeddings: Sequence[Sequence[float]],
    top_k: int,
) -> dict[str, list[str]]:
    rankings: dict[str, list[str]] = {}
    for query, embedding in zip(queries, query_embeddings, strict=True):
        response = collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, max(int(collection.count()), 1)),
        )
        raw_ids = response.get("ids", [[]])
        first = raw_ids[0] if raw_ids else []
        rankings[query.id] = [str(item_id) for item_id in first]
    return rankings


def run_onnx_sqlite_vec_benchmark(
    documents: Sequence[BenchmarkDocument],
    queries: Sequence[BenchmarkQuery],
    *,
    db_path: Path,
    top_k: int,
    iterations: int,
    model_repo: str,
    model_revision: str,
    batch_size: int,
) -> tuple[dict[str, object], dict[str, list[str]]]:
    """用轻量 tokenizer + ONNXRuntime + sqlite-vec 跑 M48.0b。"""
    rss_before = _rss_mb()

    try:
        started = time.perf_counter()
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "缺少 M48.0b 可选依赖；请从 scripts/memory-retrieval-benchmark/run.* 运行"
        ) from exc
    hub_import_ms = _elapsed_ms(started)
    rss_after_hub_import = _rss_mb()

    started = time.perf_counter()
    snapshot = Path(
        snapshot_download(
            model_repo,
            revision=model_revision,
            allow_patterns=[
                "onnx/model_quantized.onnx",
                "onnx/model_quantized.onnx_data",
                "tokenizer.json",
                "tokenizer_config.json",
            ],
        )
    )
    asset_prepare_ms = _elapsed_ms(started)
    model_path = snapshot / "onnx" / "model_quantized.onnx"
    tokenizer_path = snapshot / "tokenizer.json"
    if not model_path.exists() or not tokenizer_path.exists():
        raise RuntimeError(f"ONNX benchmark 资产不完整: {snapshot}")

    started = time.perf_counter()
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("缺少 numpy") from exc
    numpy_import_ms = _elapsed_ms(started)
    rss_after_numpy_import = _rss_mb()

    started = time.perf_counter()
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("缺少 onnxruntime") from exc
    onnxruntime_import_ms = _elapsed_ms(started)
    rss_after_onnxruntime_import = _rss_mb()

    started = time.perf_counter()
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise RuntimeError("缺少 tokenizers") from exc
    tokenizer_import_ms = _elapsed_ms(started)
    rss_after_tokenizer_import = _rss_mb()

    started = time.perf_counter()
    try:
        import sqlite_vec
    except ImportError as exc:
        raise RuntimeError("缺少 sqlite-vec") from exc
    sqlite_vec_import_ms = _elapsed_ms(started)
    rss_after_sqlite_vec_import = _rss_mb()

    started = time.perf_counter()
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    session_options = ort.SessionOptions()
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session_options.intra_op_num_threads = max(1, min(os.cpu_count() or 1, 4))
    session_options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(model_path),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    model_load_ms = _elapsed_ms(started)
    rss_after_model = _rss_mb()

    texts = [document.text for document in documents]
    started = time.perf_counter()
    document_embeddings = _onnx_embed(
        texts,
        tokenizer=tokenizer,
        session=session,
        np=np,
        batch_size=batch_size,
    )
    document_encode_ms = _elapsed_ms(started)
    dimension = int(document_embeddings.shape[1]) if len(document_embeddings.shape) == 2 else 0

    started = time.perf_counter()
    db = _open_vec_db(db_path, sqlite_vec=sqlite_vec, dimension=dimension)
    db.executemany(
        "INSERT INTO vector_ids(id, external_id) VALUES (?, ?)",
        ((index + 1, document.id) for index, document in enumerate(documents)),
    )
    db.executemany(
        "INSERT INTO vector_index(id, embedding) VALUES (?, ?)",
        (
            (index + 1, sqlite_vec.serialize_float32(vector.tolist()))
            for index, vector in enumerate(document_embeddings)
        ),
    )
    db.commit()
    index_build_ms = _elapsed_ms(started)
    del document_embeddings
    gc.collect()
    rss_after_index = _rss_mb()

    query_embeddings = _onnx_embed(
        [_QUERY_INSTRUCTION_ZH + query.text for query in queries],
        tokenizer=tokenizer,
        session=session,
        np=np,
        batch_size=batch_size,
    )
    rankings = _sqlite_vec_rankings(
        db,
        sqlite_vec=sqlite_vec,
        queries=queries,
        query_embeddings=query_embeddings,
        top_k=top_k,
    )

    embedding_latencies: list[float] = []
    index_latencies: list[float] = []
    end_to_end_latencies: list[float] = []
    for _ in range(iterations):
        for query in queries:
            end_started = time.perf_counter()
            started = time.perf_counter()
            query_vector = _onnx_embed(
                [_QUERY_INSTRUCTION_ZH + query.text],
                tokenizer=tokenizer,
                session=session,
                np=np,
                batch_size=1,
            )[0]
            embedding_latencies.append(_elapsed_ms(started))
            started = time.perf_counter()
            _sqlite_vec_search(
                db,
                sqlite_vec=sqlite_vec,
                query_vector=query_vector,
                top_k=min(top_k, len(documents)),
            )
            index_latencies.append(_elapsed_ms(started))
            end_to_end_latencies.append(_elapsed_ms(end_started))

    db.close()
    started = time.perf_counter()
    reopened = _open_vec_db(db_path, sqlite_vec=sqlite_vec, dimension=dimension)
    reopened.execute("SELECT count(*) FROM vector_index").fetchone()
    startup_ms = _elapsed_ms(started)
    reopened.close()

    selected_assets = (
        model_path,
        snapshot / "onnx" / "model_quantized.onnx_data",
        tokenizer_path,
        snapshot / "tokenizer_config.json",
    )
    result: dict[str, object] = {
        "status": "ok",
        "engine": "sqlite-vec",
        "engine_version": _package_version("sqlite-vec"),
        "embedding_library": "onnxruntime+tokenizers",
        "onnxruntime_version": _package_version("onnxruntime"),
        "tokenizers_version": _package_version("tokenizers"),
        "model_id": model_repo,
        "resolved_revision": model_revision,
        "model_variant": "onnx/model_quantized.onnx",
        "dimension": dimension,
        "query_instruction": _QUERY_INSTRUCTION_ZH,
        "quality": evaluate_rankings(queries, rankings),
        "asset_prepare_ms": round(asset_prepare_ms, 3),
        "huggingface_hub_import_ms": round(hub_import_ms, 3),
        "numpy_import_ms": round(numpy_import_ms, 3),
        "onnxruntime_import_ms": round(onnxruntime_import_ms, 3),
        "tokenizer_import_ms": round(tokenizer_import_ms, 3),
        "sqlite_vec_import_ms": round(sqlite_vec_import_ms, 3),
        "model_load_ms": round(model_load_ms, 3),
        "document_encode_ms": round(document_encode_ms, 3),
        "document_encode_per_item_ms": round(document_encode_ms / max(len(documents), 1), 6),
        "index_build_ms": round(index_build_ms, 3),
        "startup_ms": round(startup_ms, 3),
        "query_embedding_latency_ms": _latency_summary(embedding_latencies),
        "index_query_latency_ms": _latency_summary(index_latencies),
        "end_to_end_query_latency_ms": _latency_summary(end_to_end_latencies),
        "rss_before_mb": rss_before,
        "rss_after_hub_import_mb": rss_after_hub_import,
        "rss_after_numpy_import_mb": rss_after_numpy_import,
        "rss_after_onnxruntime_import_mb": rss_after_onnxruntime_import,
        "rss_after_tokenizer_import_mb": rss_after_tokenizer_import,
        "rss_after_sqlite_vec_import_mb": rss_after_sqlite_vec_import,
        "rss_after_model_mb": rss_after_model,
        "rss_after_index_mb": rss_after_index,
        "rss_model_load_delta_mb": _delta(rss_after_sqlite_vec_import, rss_after_model),
        "rss_total_delta_mb": _delta(rss_before, rss_after_index),
        "model_asset_mb": _bytes_to_mb(
            sum(path.stat().st_size for path in selected_assets if path.exists())
        ),
        "model_asset_sha256": {
            path.name: _sha256(path) for path in selected_assets if path.exists()
        },
        "index_disk_mb": _bytes_to_mb(_tree_size(db_path)),
    }
    return result, rankings


def install_onnx_assets(
    destination: Path,
    *,
    model_repo: str,
    model_revision: str,
) -> Path:
    """下载锁定 revision 的 ONNX 资产并原子写入可供生产发现的目录。"""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "缺少模型资产安装依赖；请从 scripts/memory-retrieval-benchmark/run.* 运行"
        ) from exc
    snapshot = Path(
        snapshot_download(
            model_repo,
            revision=model_revision,
            allow_patterns=[
                "onnx/model_quantized.onnx",
                "onnx/model_quantized.onnx_data",
                "tokenizer.json",
                "tokenizer_config.json",
            ],
        )
    )
    required = [snapshot / "onnx" / "model_quantized.onnx", snapshot / "tokenizer.json"]
    if any(not path.exists() for path in required):
        raise RuntimeError(f"ONNX 生产资产不完整: {snapshot}")

    staging = destination.with_name(f"{destination.name}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "onnx").mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for relative in (
        Path("onnx/model_quantized.onnx"),
        Path("onnx/model_quantized.onnx_data"),
        Path("tokenizer.json"),
        Path("tokenizer_config.json"),
    ):
        source = snapshot / relative
        if source.exists():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target)
    manifest = {
        "logical_model_id": "BAAI/bge-small-zh-v1.5",
        "source_repo": model_repo,
        "source_revision": model_revision,
        "dimension": 512,
        "files": {
            str(path.relative_to(staging)).replace("\\", "/"): _sha256(path) for path in copied
        },
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)
    return destination


def _onnx_embed(
    texts: Sequence[str],
    *,
    tokenizer: Any,
    session: Any,
    np: Any,
    batch_size: int,
) -> Any:
    vectors: list[Any] = []
    input_names = {item.name for item in session.get_inputs()}
    for offset in range(0, len(texts), batch_size):
        batch = list(texts[offset : offset + batch_size])
        encodings = tokenizer.encode_batch(batch)
        feeds: dict[str, Any] = {
            "input_ids": np.asarray([encoding.ids for encoding in encodings], dtype=np.int64),
            "attention_mask": np.asarray(
                [encoding.attention_mask for encoding in encodings], dtype=np.int64
            ),
        }
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.asarray(
                [encoding.type_ids for encoding in encodings], dtype=np.int64
            )
        last_hidden_state = session.run(None, feeds)[0]
        pooled = last_hidden_state[:, 0, :]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.maximum(norms, 1e-12)
        vectors.extend(normalized)
    return np.asarray(vectors, dtype=np.float32)


def _open_vec_db(db_path: Path, *, sqlite_vec: Any, dimension: int) -> Any:
    import sqlite3

    db = sqlite3.connect(db_path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS vector_ids ("
        "id INTEGER PRIMARY KEY, external_id TEXT NOT NULL UNIQUE)"
    )
    db.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vector_index USING vec0("
        f"id INTEGER PRIMARY KEY, embedding float[{dimension}])"
    )
    return db


def _sqlite_vec_rankings(
    db: Any,
    *,
    sqlite_vec: Any,
    queries: Sequence[BenchmarkQuery],
    query_embeddings: Any,
    top_k: int,
) -> dict[str, list[str]]:
    return {
        query.id: _sqlite_vec_search(
            db,
            sqlite_vec=sqlite_vec,
            query_vector=query_vector,
            top_k=top_k,
        )
        for query, query_vector in zip(queries, query_embeddings, strict=True)
    }


def _sqlite_vec_search(db: Any, *, sqlite_vec: Any, query_vector: Any, top_k: int) -> list[str]:
    rows = db.execute(
        """
        SELECT mapping.external_id
        FROM (
            SELECT id, distance
            FROM vector_index
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
        ) AS hits
        JOIN vector_ids AS mapping ON mapping.id = hits.id
        ORDER BY hits.distance
        """,
        (sqlite_vec.serialize_float32(query_vector.tolist()), top_k),
    ).fetchall()
    return [str(row[0]) for row in rows]


def run_hybrid_benchmark(
    queries: Sequence[BenchmarkQuery],
    keyword_rankings: Mapping[str, Sequence[str]],
    dense_rankings: Mapping[str, Sequence[str]],
    *,
    top_k: int,
    iterations: int,
) -> tuple[dict[str, object], dict[str, list[str]]]:
    """对两路候选做等权 RRF，单独记录融合开销。"""
    rankings = {
        query.id: reciprocal_rank_fusion(
            [keyword_rankings.get(query.id, ()), dense_rankings.get(query.id, ())],
            limit=top_k,
        )
        for query in queries
    }
    latencies: list[float] = []
    for _ in range(iterations):
        for query in queries:
            started = time.perf_counter()
            reciprocal_rank_fusion(
                [keyword_rankings.get(query.id, ()), dense_rankings.get(query.id, ())],
                limit=top_k,
            )
            latencies.append(_elapsed_ms(started))
    result: dict[str, object] = {
        "status": "ok",
        "engine": "rrf",
        "rank_constant": 60,
        "quality": evaluate_rankings(queries, rankings),
        "fusion_latency_ms": _latency_summary(latencies),
    }
    return result, rankings


def run_history_benchmark(
    *,
    source: str,
    synthetic_dir: Path,
    session_count: int,
    messages_per_session: int,
    iterations: int,
) -> dict[str, object]:
    """测量当前 ``recall_past_chats`` 全会话扫描，不记录任何正文。"""
    if source == "skip":
        return {"status": "skipped"}
    if source == "local":
        base_dir = sessions_dir()
        source_label = "local-user-data"
    elif source == "synthetic":
        base_dir = synthetic_dir
        _build_synthetic_sessions(base_dir, session_count, messages_per_session)
        source_label = "synthetic"
    else:
        raise ValueError(f"未知 history source: {source}")

    store = JsonlSessionStore(base_dir)
    started = time.perf_counter()
    summaries = store.list()
    list_ms = _elapsed_ms(started)
    tool = ConversationHistoryTool(store)
    latencies: list[float] = []
    result_counts: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter()
        outcome = tool.invoke(
            {"search_text": "__agent_friend_benchmark_missing_term__", "limit": 10}
        )
        latencies.append(_elapsed_ms(started))
        raw_count = outcome.meta.get("result_count", 0)
        result_counts.append(raw_count if isinstance(raw_count, int) else 0)

    files = list(base_dir.glob("*.jsonl")) if base_dir.exists() else []
    return {
        "status": "ok",
        "source": source_label,
        "session_count": len(summaries),
        "jsonl_file_count": len(files),
        "jsonl_bytes": sum(path.stat().st_size for path in files),
        "list_ms": round(list_ms, 3),
        "full_scan_latency_ms": _latency_summary(latencies),
        "result_counts": result_counts,
        "content_recorded": False,
    }


def _build_synthetic_sessions(base_dir: Path, count: int, messages_per_session: int) -> None:
    store = JsonlSessionStore(base_dir)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    for session_index in range(count):
        created_at = base_time + timedelta(days=session_index)
        session = Session.new(
            title=f"benchmark-{session_index}",
            persona="default",
            persona_id=_PERSONA_ID,
            model="fake/local",
            session_id=f"benchmark-{session_index:05d}",
            created_at=created_at,
        )
        for message_index in range(messages_per_session):
            role: Literal["user_message", "assistant_message"] = (
                "user_message" if message_index % 2 == 0 else "assistant_message"
            )
            session.append(
                Event(
                    type=role,
                    uuid=str(uuid4()),
                    ts=created_at + timedelta(minutes=message_index),
                    payload={
                        "content": (
                            f"合成历史消息 {session_index}-{message_index}，"
                            "用于测量当前逐文件扫描的固定成本。"
                        )
                    },
                    meta={},
                )
            )
        store.create(session)


def build_report(
    config: BenchmarkConfig,
    *,
    started_at: datetime,
    ended_at: datetime,
    query_count: int,
    systems: Mapping[str, object],
) -> dict[str, object]:
    """生成不含用户内容的 benchmark JSON。"""
    return {
        "schema_version": 1,
        "run": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
            "git_commit": _git_output("rev-parse", "--short", "HEAD"),
            "working_tree_dirty": bool(_git_output("status", "--porcelain")),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "corpus_size": config.corpus_size,
            "query_count": query_count,
            "top_k": config.top_k,
            "iterations": config.iterations,
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "device": config.device,
            "batch_size": config.batch_size,
            "history_source": config.history_source,
            "history_sessions": config.history_sessions,
            "history_messages_per_session": config.history_messages_per_session,
            "external_llm_calls": 0,
        },
        "systems": dict(systems),
    }


def write_report(payload: Mapping[str, object], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def default_report_path(repo_root: Path, started_at: datetime) -> Path:
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    system = platform.system().lower() or "unknown"
    return (
        repo_root
        / "docs"
        / "requirements"
        / "048-memory-retrieval-quality-pass-2"
        / "benchmark-results"
        / f"{stamp}-{system}.json"
    )


def _latency_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "p50": _rounded(percentile(values, 0.50)),
        "p95": _rounded(percentile(values, 0.95)),
        "max": _rounded(max(values) if values else None),
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _rss_mb() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return round(float(psutil.Process().memory_info().rss) / (1024 * 1024), 3)


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 3)


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_to_mb(value: int) -> float:
    return round(value / (1024 * 1024), 3)


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _resolved_model_revision(model: Any, fallback: str) -> str:
    try:
        first_module = model[0]
        config = first_module.auto_model.config
        resolved = config._commit_hash
    except (AttributeError, IndexError, TypeError):
        return fallback
    return str(resolved) if resolved else fallback


def _model_asset_mb(model_id: str, revision: str) -> float | None:
    try:
        from huggingface_hub import snapshot_download

        path = Path(snapshot_download(model_id, revision=revision, local_files_only=True))
    except (ImportError, OSError, ValueError):
        return None
    return _bytes_to_mb(_tree_size(path))


def _git_output(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    return output or None
