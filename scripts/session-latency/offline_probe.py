"""Offline sending through the real runtime on disposable copies of explicit fixtures."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import hashlib
import json
import logging
import os
import platform
import runpy
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]


def deny_network(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("offline_send_network_forbidden")


def close_offline_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Drain cleanup scheduled by import-time SDK exit hooks, then release our loop."""
    try:
        pending = asyncio.all_tasks(loop)
        if pending:
            with suppress(TimeoutError):
                loop.run_until_complete(
                    asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=1)
                )
    finally:
        loop.close()


def copy_fixture(source: Path, target: Path) -> dict[str, Any]:
    """Copy first, then measure. Never open the caller's fixture for writing."""
    before = source.stat()
    with source.open("rb") as handle:
        first = json.loads(handle.readline())
    if first.get("type") != "session_meta":
        raise ValueError("fixture_requires_session_meta")
    session_id = str(UUID(first["uuid"]))
    destination = target / f"{session_id}.jsonl"
    with source.open("rb") as original, destination.open("xb") as copied:
        shutil.copyfileobj(original, copied)
    with destination.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("fixture_changed_during_copy")
    return {"session_id": session_id, "bytes": after.st_size, "sha256": digest}


class Measurements(logging.Handler):
    """An allowlist, not a dump of logs that can contain private conversations."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self.run_id = "setup"
        self.truncated = False

    def emit(self, record: logging.LogRecord) -> None:
        from agent.latency import current_run_phase

        row = getattr(record, "run_latency", None)
        if row is not None:
            # Only timing/counter fields. Existing transport logs carry additional
            # provider identifiers and witnesses which this report does not need.
            keys = (
                "event",
                "run_id",
                "run_elapsed_ms",
                "phase",
                "phase_id",
                "parent_phase_id",
                "started_elapsed_ms",
                "duration_ms",
                "thread_cpu_ms",
                "status",
                "error_type",
                "queue_ms",
                "pre_submit_ms",
                "call_index",
            )
            row = {key: row[key] for key in keys if key in row}
        elif hasattr(record, "session_file_operation"):
            keys = (
                "session_file_operation",
                "session_key",
                "wait_ms",
                "hold_ms",
                "parse_ms",
                "byte_count",
                "outcome",
                "phases",
                "caller",
            )
            row = {key: getattr(record, key) for key in keys}
            row["event"] = "session_file"
        elif hasattr(record, "cache_action"):
            keys = (
                "session_key",
                "cache_action",
                "projection_kind",
                "cache_miss_reason",
                "query_wait_ms",
                "query_total_ms",
                "outcome",
            )
            row = {key: getattr(record, key) for key in keys}
            row["event"] = "session_query"
        elif hasattr(record, "memory_search_phase"):
            row = {
                "event": "memory_source",
                "phase": record.memory_search_phase,
                "duration_ms": record.memory_duration_ms,
            }
        else:
            return
        row = dict(row)
        row.setdefault("run_id", self.run_id)
        parent = current_run_phase()
        if parent is not None and "phase_id" not in row:
            row["enclosing_phase_id"] = parent[1]
        if len(self.rows) < 10000:
            self.rows.append(row)
        else:
            self.truncated = True


def measure(
    directory: Path, fixture_ids: dict[str, str], sequence: list[str], *, reload_each: bool = False
) -> dict[str, Any]:
    from datetime import UTC, datetime

    from agent.latency import monotonic_ms
    from agent.memory_search import SessionHistorySource, SessionStoreSourceOrderProvider
    from agent.runtime import AgentRuntime, UserEvent
    from agent.system_prompt import DESKTOP_CHAT_SURFACE
    from memory.retrieval.renderer import Renderer
    from memory.search import SearchRequest

    from agent import (
        BUILTIN_DEFAULT_PERSONA_ID,
        JsonlSessionStore,
        MarkdownPromptBuilder,
        NaiveContextManager,
        PersonaCatalog,
        SessionManager,
    )
    from llm_providers import LLMTextDelta, LLMTurnDone, ProviderAssistantTurn
    from memory import MemorySearchService

    collector = Measurements()
    root_logger = logging.getLogger()
    measured_loggers = {
        root_logger: logging.INFO,
        logging.getLogger("agent.sessions.file_observation"): logging.DEBUG,
        logging.getLogger("agent.sessions.query_observation"): logging.DEBUG,
    }
    previous_levels = {logger: logger.level for logger in measured_loggers}
    for logger, level in measured_loggers.items():
        logger.setLevel(level)
    root_logger.addHandler(collector)
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    memory_metrics = runpy.run_path(str(ROOT / "scripts/session-memory/run.py"))["memory"]

    class OfflineLLM:
        context_window = 2_000_000

        def generate(self, _request: Any) -> Any:
            raise AssertionError("offline_unexpected_nonstream_model_call")

        def stream(self, request: Any) -> Any:
            current["provider_calls"] += 1
            if current["provider_calls"] == 1:
                current["first_fake_request_ms"] = monotonic_ms() - current["started_ms"]
                # Measure wire-like JSON separately; this is not an SDK transport.
                encoding_started = time.perf_counter()
                body = json.dumps(
                    request.messages, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                current["request_json_ms"] = (time.perf_counter() - encoding_started) * 1000
                current["request_json_bytes"] = len(body)
            yield LLMTextDelta(text="offline probe completed")
            yield LLMTurnDone(
                stop_reason="end_turn",
                assistant_turn=ProviderAssistantTurn(content="offline probe completed"),
            )

    class OfflineRecall:
        def __init__(self, store: Any) -> None:
            self.service = MemorySearchService(
                [SessionHistorySource(store)],
                source_order_provider=SessionStoreSourceOrderProvider(store),
            )

        def retrieve(self, query: str, **kwargs: Any) -> Any:
            result = self.service.search(
                SearchRequest(
                    text=query,
                    owner_user_id="default",
                    persona_id=kwargs["persona_id"],
                    mode="automatic",
                    session_id=kwargs["session_id"],
                    automatic_recall_scope=kwargs.get("automatic_recall_scope"),
                ),
                cancel_check=kwargs.get("cancel_check"),
            )
            current["recall_evidence_count"] = len(result.evidence)
            current["recall_failure_count"] = len(result.failures)
            return Renderer().render_search(result.evidence, now=datetime.now(UTC))

    manager: Any = None
    runtime: Any = None

    def build() -> tuple[Any, Any]:
        store = JsonlSessionStore(directory / "sessions")
        catalog = PersonaCatalog(external_dir=directory / "personas")
        manager = SessionManager(
            store=store,
            llm_client_factory=lambda _config: OfflineLLM(),
            prompt_builder_factory=lambda _pid: MarkdownPromptBuilder(
                persona_id=BUILTIN_DEFAULT_PERSONA_ID, catalog=catalog
            ),
            context_manager_factory=NaiveContextManager,
            memory=OfflineRecall(store),
        )
        runtime = AgentRuntime(
            conversation_factory=lambda sid: manager.start_conversation(
                manager.open_for_execution(sid), post_turn_external=True
            ),
            max_workers=1,
        )
        runtime.start()
        return manager, runtime

    try:
        manager, runtime = build()
        for index, alias in enumerate(sequence):
            if reload_each and index:
                runtime.stop()
                manager.close()
                manager, runtime = build()
            sid = fixture_ids[alias]
            collector.run_id = f"load-{index + 1}"
            # Preload execution state to separate first load from hot send. Query
            # caches remain naturally shared and can still evict one another.
            load_started = time.perf_counter()
            conv = manager.start_conversation(
                manager.open_for_execution(sid), post_turn_external=True
            )
            load_ms = (time.perf_counter() - load_started) * 1000
            collector.run_id = f"offline-{index + 1}"
            current = {
                "run_id": collector.run_id,
                "sample": alias,
                "load_ms": load_ms,
                "started_ms": monotonic_ms(),
                "provider_calls": 0,
            }
            handle = runtime.submit(
                UserEvent(session_id=sid, user_input="请回顾之前的对话和已经完成的工作"),
                run_id=collector.run_id,
                latency_started_ms=current["started_ms"],
                channel="text",
                surface=DESKTOP_CHAT_SURFACE,
                conversation_preparer=lambda _cancel, conv=conv: conv,
            )
            terminal = handle.wait(timeout=180)
            if terminal is None:
                handle.cancel()
                raise TimeoutError("offline_send_timeout")
            current["outcome"] = terminal.outcome.value
            current["error_type"] = (
                type(terminal.error).__name__ if terminal.error is not None else None
            )
            current["process_memory"] = memory_metrics()
            projection_files = tuple((directory / "sessions" / ".recall").glob("*.sqlite3"))
            current["recall_projection"] = {
                "database_count": len(projection_files),
                "database_bytes": sum(path.stat().st_size for path in projection_files),
            }
            current.pop("started_ms")
            runs.append(current)
            if current["outcome"] != "completed" or current["provider_calls"] != 1:
                break
    finally:
        if runtime is not None:
            runtime.stop()
        if manager is not None:
            manager.close()
        root_logger.removeHandler(collector)
        for logger, level in previous_levels.items():
            logger.setLevel(level)
    return {
        "runs": runs,
        "observations": collector.rows,
        "observations_truncated": collector.truncated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", action="append", required=True, help="ALIAS=explicit JSONL file (only copied)"
    )
    parser.add_argument(
        "--sequence",
        required=True,
        help="Comma-separated fixture aliases; natural cache state is preserved",
    )
    parser.add_argument(
        "--reload-each",
        action="store_true",
        help="Recreate runtime and stores per send; does not clear OS cache",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New numeric report file; never overwritten"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    fixtures: dict[str, Path] = {}
    for item in args.fixture:
        alias, separator, path = item.partition("=")
        if not separator or not alias.isalnum() or alias in fixtures:
            parser.error("fixtures need unique alphanumeric aliases")
        fixtures[alias] = Path(path).resolve(strict=True)
    sequence = args.sequence.split(",")
    if not sequence or len(sequence) > 100 or any(alias not in fixtures for alias in sequence):
        parser.error("sequence must contain 1..100 known aliases")
    # Windows event loops need a local control socket pair. Initialize it before
    # blocking connections, and close it after subsequently registered SDK cleanup.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    atexit.register(close_offline_loop, loop)
    socket.socket.connect = deny_network  # type: ignore[method-assign]
    socket.socket.connect_ex = deny_network  # type: ignore[method-assign]
    socket.create_connection = deny_network
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "offline_send_baseline",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "configuration": {
            "context_strategy": "naive",
            "fake_context_window": 2_000_000,
            "query_cache_bytes": 64 * 1024 * 1024,
            "workers": 1,
            "structured_memory": False,
            "active_skills": False,
            "tools": False,
            "logging": "INFO, with file/query observation DEBUG only",
        },
        "production_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "sequence": sequence,
        "reload_each": args.reload_each,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": [
            "fake provider: first request is not upstream receipt",
            "real history source and scope; no structured memory database or extraction",
            "default builtin persona, NaiveContextManager and no active Skill service/tools/attachments",
            "no UI, HTTP/IPC, real provider adapter, or OS cold-cache measurement",
            "execution preload reported separately; sends mutate disposable copies",
        ],
    }
    # A newly owned TemporaryDirectory is the only recursive cleanup target.
    with tempfile.TemporaryDirectory(prefix="af-offline-send-") as temporary:
        directory = Path(temporary).resolve()
        os.environ["AGENT_FRIEND_DATA_DIR"] = str(directory)
        os.environ["AGENT_FRIEND_LOG_DIR"] = str(directory / "logs")
        sessions = directory / "sessions"
        sessions.mkdir()
        copies = {alias: copy_fixture(source, sessions) for alias, source in fixtures.items()}
        fixture_ids = {alias: item["session_id"] for alias, item in copies.items()}
        report["fixtures"] = {
            alias: {key: value for key, value in item.items() if key != "session_id"}
            for alias, item in copies.items()
        }
        # Imports and application logs may contain private values. Only the
        # allowlisted collector is retained; ordinary logs are not persisted.
        logging.getLogger().addHandler(logging.NullHandler())
        report.update(measure(directory, fixture_ids, sequence, reload_each=args.reload_each))
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    report["tracked_overlay_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in changed
        if (ROOT / name).is_file()
    }
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    report["untracked_overlay_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in untracked
        if (ROOT / name).is_file()
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    ok = (
        len(report["runs"]) == len(sequence)
        and all(
            row["outcome"] == "completed"
            and row["provider_calls"] == 1
            and row.get("recall_failure_count") == 0
            for row in report["runs"]
        )
        and not report["observations_truncated"]
    )
    print(json.dumps({"ok": ok, "runs": report["runs"]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
