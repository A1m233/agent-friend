"""Concurrent persistence probe on a temporary copy; never mutates the source profile."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent.notifications import NotificationFact, NotificationStore
from agent.paths import sessions_dir

from agent import Event, JsonlSessionStore


class Observations(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rows.append(
            {
                key: getattr(record, key)
                for key in (
                    "session_file_operation",
                    "wait_ms",
                    "hold_ms",
                    "parse_ms",
                    "byte_count",
                    "outcome",
                    "phases",
                )
            }
        )


def process_reads(
    directory: str, session_id: str, query: bool, start: object, queue: object
) -> None:
    """Diagnostic subprocess only; no product runtime process design is implied."""
    store = JsonlSessionStore(Path(directory))
    queue.put("ready")
    start.wait(timeout=30)
    for _ in range(2):
        if query:
            store.query(session_id)
        else:
            store.load(session_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--serialize-readers", action="store_true")
    parser.add_argument("--reader-processes", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    # Resolve through the real store; no caller-provided paths or session contents in report.
    source = JsonlSessionStore(sessions_dir())._existing_path(args.session_id)
    if source is None:
        parser.error("session not found")
    before = source.stat()
    capture = Observations()
    logger = logging.getLogger("agent.sessions.file_observation")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(capture)
    with tempfile.TemporaryDirectory(prefix="agent-friend-session-lock-probe-") as temporary:
        target = Path(temporary)
        copied = target / f"{args.session_id}.jsonl"
        shutil.copyfile(source, copied)
        if (
            source.stat().st_mtime_ns != before.st_mtime_ns
            or copied.stat().st_size != before.st_size
        ):
            raise RuntimeError("source changed while copying; retry after source is idle")
        store = JsonlSessionStore(target)
        notifications = NotificationStore(target / "notifications.db")
        gate = threading.Barrier(4 if args.reader_processes else 5)
        write_times: list[float] = []
        written = [str(uuid4()) for _ in range(24)]
        store.append_events(
            args.session_id,
            [
                Event(
                    type="tool_call_request",
                    uuid=f"request-{event_id}",
                    ts=datetime.now(UTC),
                    payload={
                        "tool_call_id": event_id,
                        "event_tool_call_id": event_id,
                        "tool_name": "exec_command",
                        "args": {},
                    },
                    meta={"run_id": "isolated-lock-probe"},
                )
                for event_id in written
            ],
        )
        read_gate = threading.Lock()

        def read(query: bool) -> None:
            gate.wait(timeout=10)
            for _ in range(2):
                if args.serialize_readers:
                    read_gate.acquire()
                try:
                    if query:
                        store.query(args.session_id)
                    else:
                        store.load(args.session_id)
                finally:
                    if args.serialize_readers:
                        read_gate.release()

        def write() -> None:
            gate.wait(timeout=10)
            for event_id in written:
                started = time.perf_counter()
                store.append_event(
                    args.session_id,
                    Event(
                        type="tool_call_result",
                        uuid=event_id,
                        ts=datetime.now(UTC),
                        payload={
                            "tool_call_id": event_id,
                            "event_tool_call_id": event_id,
                            "tool_name": "exec_command",
                            "result": "isolated persistence probe",
                            "is_error": False,
                        },
                        meta={"run_id": "isolated-lock-probe"},
                    ),
                )
                write_times.append((time.perf_counter() - started) * 1000)
                time.sleep(0.02)

        def notify(write: bool) -> None:
            gate.wait(timeout=10)
            for index in range(40):
                if write:
                    notifications.accept(
                        NotificationFact(
                            event_key=f"probe-{index}",
                            kind="reply_ready",
                            title="probe",
                            persona_id="probe",
                            session_id="probe",
                            run_id=str(index),
                            created_at=datetime.now(UTC),
                            owner_user_id="local",
                        ),
                        provided_text="probe",
                    )
                else:
                    notifications.list()

        processes = []
        if args.reader_processes:
            context = multiprocessing.get_context("spawn")
            start_readers, ready = context.Event(), context.Queue()
            for query in (False, True):
                process = context.Process(
                    target=process_reads,
                    args=(str(target), args.session_id, query, start_readers, ready),
                )
                process.start()
                processes.append(process)
            for _ in processes:
                assert ready.get(timeout=30) == "ready"
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(write), pool.submit(notify, False), pool.submit(notify, True)]
            if args.reader_processes:
                gate.wait(timeout=10)
                start_readers.set()
            else:
                futures += [pool.submit(read, False), pool.submit(read, True)]
            for future in futures:
                future.result(timeout=90)
        for process in processes:
            process.join(timeout=90)
            if process.is_alive():
                process.terminate()
                process.join()
                raise RuntimeError("diagnostic reader timed out")
            if process.exitcode != 0:
                raise RuntimeError("diagnostic reader failed")
        elapsed = (time.perf_counter() - started) * 1000
        persisted = store.load(args.session_id)
        actual = [event.uuid for event in persisted.events if event.uuid in set(written)]
        equivalent = actual == written and len(notifications.list()) == 40
        result_call_ids = [
            event.payload["event_tool_call_id"]
            for event in persisted.events
            if event.uuid in set(written) and event.type == "tool_call_result"
        ]
        equivalent = equivalent and result_call_ids == written
        report = {
            "sourceBytes": before.st_size,
            "elapsedMs": elapsed,
            "writeMs": write_times,
            "fileOperations": capture.rows,
            "jsonlAppends": len(actual),
            "toolResults": len(result_call_ids),
            "notificationWrites": 40,
            "passed": equivalent,
            "serializeReaders": args.serialize_readers,
            "readerProcesses": args.reader_processes,
            "limits": "Two full reads, query reads and writes overlap with notification reads/writes. readerProcesses and serializeReaders specify the diagnostic variant. Temporary copy and synthetic notifications; no LLM or original-profile writes; timings depend on external system load.",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
        print(
            json.dumps({k: v for k, v in report.items() if k not in {"fileOperations", "writeMs"}})
        )
        return 0 if equivalent else 1


if __name__ == "__main__":
    raise SystemExit(main())
