"""Read an explicit old journal on an isolated copy; emit hashes/counts, never content."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import hashlib
import json
import os
import shutil
import socket
import tempfile
import time
from collections import Counter
from pathlib import Path

from offline_probe import close_offline_loop


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-compare", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if args.output.exists() or args.repeats < 1:
        parser.error("output must not exist; repeats must be positive")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    atexit.register(close_offline_loop, loop)

    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy read probe forbids network and full hot-path reads")

    socket.socket.connect = denied  # type: ignore[method-assign]
    socket.create_connection = denied  # type: ignore[assignment]
    from agent.sessions.read_views.display import display_event

    from agent import JsonlSessionStore

    report: dict[str, object] = {"kind": "isolated_legacy_read", "real_llm_calls": 0}
    before = args.source.stat()
    with tempfile.TemporaryDirectory(prefix="af-172-legacy-") as temporary:
        store = JsonlSessionStore(Path(temporary))
        with args.source.open("rb") as source:
            head = json.loads(source.readline())
        sid = head["uuid"]
        target = store._path(sid)
        shutil.copyfile(args.source, target)
        with target.open("rb") as copied:
            report["journal_sha256"] = hashlib.file_digest(copied, "sha256").hexdigest()
        report["journal_bytes"] = target.stat().st_size
        store.query = denied  # type: ignore[method-assign]
        store._read_records = denied  # type: ignore[method-assign]
        started = time.perf_counter()
        first = store.history_window(sid, turn_limit=10)
        report["first_page_ms"] = (time.perf_counter() - started) * 1000
        samples = []
        for _ in range(args.repeats - 1):
            started = time.perf_counter()
            repeated = JsonlSessionStore(Path(temporary)).history_window(sid, turn_limit=10)
            samples.append((time.perf_counter() - started) * 1000)
            assert repeated.active_events == first.active_events
        report["reopened_page_samples_ms"] = samples
        report["first_turns"] = sum(
            e.type in {"user_message", "system_trigger"} for e in first.active_events
        )
        report["first_events"] = len(first.active_events)
        report["event_counts"] = dict(Counter(e.type for e in first.active_events))
        report["display_event_bytes"] = sum(len(e.to_jsonl().encode()) for e in first.active_events)
        by_kind: dict[str, dict[str, int]] = {}
        for event in first.active_events:
            sizes = by_kind.setdefault(event.type, {"payload_bytes": 0, "meta_bytes": 0})
            sizes["payload_bytes"] += len(json.dumps(event.payload, ensure_ascii=False).encode())
            sizes["meta_bytes"] += len(json.dumps(event.meta, ensure_ascii=False).encode())
        report["bytes_by_event_type"] = by_kind
        from agent_bridge.routes.meta import _public_event_dict
        from fastapi.encoders import jsonable_encoder

        started = time.perf_counter()
        public_bytes = json.dumps(
            jsonable_encoder(
                [_public_event_dict(event, chat_view=True) for event in first.active_events]
            ),
            ensure_ascii=False,
        ).encode()
        report["public_encoding_ms"] = (time.perf_counter() - started) * 1000
        report["public_event_bytes"] = len(public_bytes)
        if first.history_cursor and first.history_cursor.startswith("tail-"):
            report["journal_tail_span_bytes"] = target.stat().st_size - int(
                first.history_cursor.split(":")[1]
            )
        report["first_page_sha256"] = hashlib.sha256(
            "\n".join(e.to_jsonl() for e in first.active_events).encode()
        ).hexdigest()
        started = time.perf_counter()
        config = store.composer_session(sid)
        report["legacy_configuration_bootstrap_ms"] = (time.perf_counter() - started) * 1000
        fresh = JsonlSessionStore(Path(temporary))
        started = time.perf_counter()
        assert fresh.composer_session(sid).current_model_config == config.current_model_config
        report["configuration_after_restart_ms"] = (time.perf_counter() - started) * 1000
        if first.history_cursor:
            older = store.history_window(
                sid, turn_limit=30, before=first.history_cursor, history_epoch=first.history_epoch
            )
            report["older_turns"] = sum(
                e.type in {"user_message", "system_trigger"} for e in older.active_events
            )
            assert not {e.uuid for e in first.active_events} & {e.uuid for e in older.active_events}
        if args.full_compare:
            expected = fresh.query_page(sid, turn_limit=10)
            assert first.active_events == tuple(display_event(e) for e in expected.active_events)
            report["full_public_projection_equal"] = True
        with target.open("rb") as copied:
            assert hashlib.file_digest(copied, "sha256").hexdigest() == report["journal_sha256"]
    after = args.source.stat()
    assert (before.st_size, before.st_mtime_ns, before.st_ino) == (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    )
    report["original_unchanged"] = True
    report["copy_business_bytes_unchanged"] = True
    report["limits"] = [
        "storage only, OS cache uncontrolled, source journal copied without old derived caches",
        "no messages, source path, raw session id or model configuration in report",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
