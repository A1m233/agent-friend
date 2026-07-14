"""M48.0 benchmark CLI；不会调用外部 LLM。"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agent.paths import memory_db_path

from .fixtures import benchmark_documents, benchmark_queries
from .runner import (
    BenchmarkConfig,
    build_report,
    default_report_path,
    install_onnx_assets,
    run_dense_benchmark,
    run_history_benchmark,
    run_hybrid_benchmark,
    run_keyword_benchmark,
    run_onnx_sqlite_vec_benchmark,
    write_report,
)

_DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
_DEFAULT_ONNX_MODEL = "onnx-community/bge-small-zh-v1.5-ONNX"
_DEFAULT_ONNX_REVISION = "9507db33464b5da99a532ac26b2a251767cbc62b"
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="memory-retrieval-benchmark",
        description="M48.0 本地 FTS / Chroma+BGE / RRF / 历史扫描 benchmark（0 次外部 LLM）",
    )
    parser.add_argument("--corpus-size", type=int, default=1000)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--model-revision", default="main")
    parser.add_argument(
        "--dense-engine",
        choices=("onnx-sqlite-vec", "sentence-transformers-chroma"),
        default="onnx-sqlite-vec",
    )
    parser.add_argument("--onnx-model", default=_DEFAULT_ONNX_MODEL)
    parser.add_argument("--onnx-model-revision", default=_DEFAULT_ONNX_REVISION)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--history-source",
        choices=("synthetic", "local", "skip"),
        default="synthetic",
        help="local 只记录规模和延迟，不写入会话正文",
    )
    parser.add_argument("--history-sessions", type=int, default=100)
    parser.add_argument("--history-messages-per-session", type=int, default=40)
    parser.add_argument("--skip-dense", action="store_true")
    parser.add_argument(
        "--install-production-assets",
        nargs="?",
        type=Path,
        const=memory_db_path().parent / "models" / "bge-small-zh-v1.5",
        default=None,
        help="只安装锁定的 ONNX 资产；可选目标目录，省略时写入标准用户数据目录",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.install_production_assets is not None:
        try:
            destination = install_onnx_assets(
                args.install_production_assets,
                model_repo=args.onnx_model,
                model_revision=args.onnx_model_revision,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"production embedding assets installed: {destination}")
        return 0
    if args.iterations < 1 or args.top_k < 1 or args.batch_size < 1:
        print("iterations、top-k、batch-size 必须大于 0", file=sys.stderr)
        return 2
    if args.history_sessions < 0 or args.history_messages_per_session < 0:
        print("history 规模参数不能为负数", file=sys.stderr)
        return 2

    try:
        documents = benchmark_documents(args.corpus_size)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    queries = benchmark_queries()
    selected_model = (
        args.model if args.dense_engine == "sentence-transformers-chroma" else args.onnx_model
    )
    selected_revision = (
        args.model_revision
        if args.dense_engine == "sentence-transformers-chroma"
        else args.onnx_model_revision
    )
    config = BenchmarkConfig(
        corpus_size=args.corpus_size,
        top_k=args.top_k,
        iterations=args.iterations,
        model_id=selected_model,
        model_revision=selected_revision,
        device=args.device,
        batch_size=args.batch_size,
        history_source=args.history_source,
        history_sessions=args.history_sessions,
        history_messages_per_session=args.history_messages_per_session,
    )
    started_at = datetime.now(UTC)
    systems: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="memory-retrieval-benchmark-") as tmp:
        work_dir = Path(tmp)
        print(f"[1/4] FTS5 baseline: corpus={len(documents)}, queries={len(queries)}")
        keyword, keyword_rankings = run_keyword_benchmark(
            documents,
            queries,
            db_path=work_dir / "keyword.db",
            top_k=args.top_k,
            iterations=args.iterations,
        )
        systems["keyword"] = keyword

        dense_rankings: dict[str, list[str]] | None = None
        if args.skip_dense:
            print("[2/4] dense benchmark skipped")
            systems["dense"] = {"status": "skipped"}
            systems["hybrid"] = {"status": "skipped"}
        else:
            print(f"[2/4] dense engine: {args.dense_engine}")
            try:
                if args.dense_engine == "sentence-transformers-chroma":
                    dense, dense_rankings = run_dense_benchmark(
                        documents,
                        queries,
                        index_dir=work_dir / "chroma",
                        top_k=args.top_k,
                        iterations=args.iterations,
                        model_id=args.model,
                        model_revision=args.model_revision,
                        device=args.device,
                        batch_size=args.batch_size,
                    )
                else:
                    dense, dense_rankings = run_onnx_sqlite_vec_benchmark(
                        documents,
                        queries,
                        db_path=work_dir / "vectors.db",
                        top_k=args.top_k,
                        iterations=args.iterations,
                        model_repo=args.onnx_model,
                        model_revision=args.onnx_model_revision,
                        batch_size=args.batch_size,
                    )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            systems["dense"] = dense
            print("[3/4] RRF fusion")
            hybrid, _hybrid_rankings = run_hybrid_benchmark(
                queries,
                keyword_rankings,
                dense_rankings,
                top_k=args.top_k,
                iterations=args.iterations,
            )
            systems["hybrid"] = hybrid

        print(f"[4/4] history scan: {args.history_source}")
        systems["history_scan"] = run_history_benchmark(
            source=args.history_source,
            synthetic_dir=work_dir / "sessions",
            session_count=args.history_sessions,
            messages_per_session=args.history_messages_per_session,
            iterations=args.iterations,
        )

    ended_at = datetime.now(UTC)
    report = build_report(
        config,
        started_at=started_at,
        ended_at=ended_at,
        query_count=len(queries),
        systems=systems,
    )
    output = args.output or default_report_path(_REPO_ROOT, started_at)
    path = write_report(report, output)
    print(f"benchmark report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
