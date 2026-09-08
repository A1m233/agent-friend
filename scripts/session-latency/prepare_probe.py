"""Profile cold manager preparation on a private copy; no turn or network allowed."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import os
import pstats
import shutil
import socket
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    from agent.paths import personas_dir, sessions_dir

    source = sessions_dir() / f"{args.session_id}.jsonl"
    before = source.stat()
    root = Path(__file__).resolve().parents[2]
    code_hashes = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in (
            "agent/src/agent/conversation.py",
            "agent/src/agent/sessions/manager.py",
            "agent/src/agent/sessions/session.py",
            "agent/src/agent/sessions/store.py",
            "agent/src/agent/sessions/rollout.py",
            "llm_providers/src/llm_providers/responses/request.py",
            "scripts/session-latency/prepare_probe.py",
        )
    }
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / "data" / "sessions"
    target.mkdir(parents=True)
    shutil.copyfile(source, target / source.name)
    shutil.copytree(personas_dir(), directory / "data" / "personas")
    with source.open("rb") as file:
        source_hash = hashlib.file_digest(file, "sha256").hexdigest()
    os.environ.update(
        AGENT_FRIEND_DATA_DIR=str(directory / "data"),
        AGENT_FRIEND_LOG_DIR=str(directory / "logs"),
    )

    import httpx

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("offline_prepare_probe_network_forbidden")

    # Windows asyncio needs loopback IPC. HTTP is blocked even on loopback.
    original_connect = socket.socket.connect

    def connect(sock: socket.socket, address: object) -> None:
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            blocked()
        original_connect(sock, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = blocked  # type: ignore[method-assign,assignment]
    httpx.HTTPTransport.handle_request = blocked  # type: ignore[assignment]
    httpx.AsyncHTTPTransport.handle_async_request = blocked  # type: ignore[assignment]
    from agent_bridge.assembly import build_runtime
    from agent_bridge.settings import BridgeSettings

    runtime = build_runtime(BridgeSettings(memory_enabled=False, im_enabled=False))
    rows = []
    try:
        manager = runtime.persistent_session_manager
        profile = cProfile.Profile()
        started = time.perf_counter()
        session = profile.runcall(manager.open, args.session_id)
        rows.append({"phase": "open", "ms": (time.perf_counter() - started) * 1000})
        started = time.perf_counter()
        conversation = profile.runcall(manager.start_conversation, session)
        rows.append({"phase": "start_conversation", "ms": (time.perf_counter() - started) * 1000})
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(65)
        (directory / "profile.txt").write_text(stream.getvalue(), encoding="utf-8")
        profile.dump_stats(str(directory / "profile.pstats"))
        from llm_providers.responses.request import convert_messages

        history = [message.to_openai() for message in conversation._session_model_history.items]
        wire = convert_messages(history)
        calls = [item["call_id"] for item in wire if item["type"] == "function_call"]
        outputs = [item["call_id"] for item in wire if item["type"] == "function_call_output"]
        assert len(calls) == len(set(calls))
        assert len(outputs) == len(set(outputs))
        assert set(outputs) <= set(calls)
        # Every projected result retains its original output in order.
        assert [item["output"] for item in wire if item["type"] == "function_call_output"] == [
            message["content"] for message in history if message["role"] == "tool"
        ]
        projection = {"messages": len(history), "calls": len(calls), "outputs": len(outputs)}
    finally:
        runtime.close()
    assert (source.stat().st_size, source.stat().st_mtime_ns) == (
        before.st_size,
        before.st_mtime_ns,
    )
    report = {
        "codeSha256": code_hashes,
        "sourceBytes": before.st_size,
        "sourceSha256": source_hash,
        "phases": rows,
        "responsesHistoryProjection": projection,
        "limits": "cProfile overhead; isolated sessions/personas copy, no original memory/attachments; memory and IM disabled; no app lifespan workers or conversation execution; HTTPX and non-loopback socket connections blocked.",
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
