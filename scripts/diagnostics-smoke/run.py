"""Deterministic diagnostic range benchmark; synthetic data only, no network."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path

from agent.command_runtime.sandbox_evidence import SandboxEvidence
from agent.sessions.diagnostic_export import export_prefix, stream_prefix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native",
        action="store_true",
        help="Also measure the actual Rust collector and ZIP writer",
    )
    parser.add_argument(
        "--native-optimized",
        action="store_true",
        help="Optimize both baseline and production collectors in the test profile",
    )
    parser.add_argument("--output", type=Path, help="Write the synthetic measurement JSON")
    parser.add_argument(
        "--desktop-seed", type=Path, help="Create a NEW isolated desktop fixture root"
    )
    parser.add_argument(
        "--desktop-check", type=Path, help="Inspect the real Tauri WebView into a fixture directory"
    )
    parser.add_argument("--desktop-action", default="inspect")
    parser.add_argument(
        "--desktop-verify",
        type=Path,
        help="Verify desktop-produced ZIPs and save sanitized evidence with --output",
    )
    parser.add_argument(
        "--desktop-augment", type=Path, help="Add large sessions/attachments to an owned fixture"
    )
    parser.add_argument(
        "--desktop-pid", type=int, help="Task-owned shell PID for sampled RSS/staging measurements"
    )
    args = parser.parse_args()
    if args.desktop_verify:
        from desktop_verify import verify

        verify(args.desktop_verify, args.output)
        return
    if args.desktop_augment:
        from desktop_fixture import augment

        augment(args.desktop_augment)
        return
    if args.desktop_check:
        repo = Path(__file__).resolve().parents[2]
        command = [
            "node",
            str(repo / "scripts/diagnostics-smoke/desktop.mjs"),
            str(args.desktop_check),
            args.desktop_action,
        ]
        if args.desktop_pid:
            from desktop_resources import run

            raise SystemExit(
                run(command, repo, args.desktop_check, args.desktop_pid, args.desktop_action)
            )
        raise SystemExit(subprocess.call(command, cwd=repo))
    if args.desktop_seed:
        from datetime import UTC, datetime
        from uuid import uuid4

        from agent.sessions.events import Event
        from agent.sessions.session import Session
        from agent.sessions.store import JsonlSessionStore

        root = args.desktop_seed.resolve()
        root.mkdir(parents=True, exist_ok=False)
        session = Session.new("诊断验收 · 合成的 20 轮对话", "default", "test/synthetic")
        store = JsonlSessionStore(root / "data" / "sessions")
        store.create(session)
        events = []
        for index in range(20):
            for kind in ("user_message", "assistant_message"):
                events.append(
                    Event(
                        type=kind,
                        uuid=str(uuid4()),
                        ts=datetime.now(UTC),
                        payload={"content": f"synthetic turn {index + 1}"},
                    )
                )
        store.append_events(session.session_id, events)
        logs = root / "logs"
        logs.mkdir()
        line = b"2026-11-28 INFO diagnostic desktop synthetic log Authorization: Bearer synthetic-secret\n"
        with (logs / "runtime-bridge.log").open("wb") as output:
            block = line * (1024 * 1024 // len(line))
            for _ in range(256):
                output.write(block)
        receipt = {
            "synthetic": True,
            "root": str(root),
            "sessionId": session.session_id,
            "turns": 20,
            "logBytes": (logs / "runtime-bridge.log").stat().st_size,
        }
        (root / "fixture.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps(receipt, indent=2))
        return
    observations = []
    with tempfile.TemporaryDirectory(prefix="agent-friend-diagnostics-smoke-") as directory:
        root = Path(directory)
        evidence = SandboxEvidence(root / "logs" / "health.json", root / "home", None, "r", "b")
        evidence.finish(evidence.begin("start"), "orchestrator_helper_incomplete")
        repair = evidence.begin("repair")
        evidence.repair_started(repair)
        evidence.finish(repair, persist_success=True)
        assert len(evidence.snapshot()["incidents"]) == 2
        for old_turns in (2048, 20480):
            path = root / "fixture.jsonl"
            with path.open("wb") as stream:
                stream.write(b'{"type":"session_meta","uuid":"meta","payload":{}}\n')
                for index in range(old_turns + 10):
                    content = "old-history-" + "x" * 8192 if index < old_turns else "recent"
                    stream.write(
                        json.dumps(
                            {
                                "type": "user_message",
                                "uuid": str(index),
                                "payload": {"content": content},
                            }
                        ).encode()
                        + b"\n"
                    )
            source_bytes = path.stat().st_size
            started = time.perf_counter()
            with path.open("rb") as stream:
                projection = export_prefix(stream, source_bytes, 10)
            body = json.dumps(projection, ensure_ascii=False).encode()
            assert b"old-history-" not in body
            assert projection["coverage"]["includedTurns"] == 10
            assert projection["coverage"]["readBytes"] < 128 * 1024
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("sessions/session-001.json", body)
                bundle.writestr(
                    "diagnostics/command-runtime-incidents.json", json.dumps(evidence.snapshot())
                )
            with zipfile.ZipFile(archive) as bundle:
                assert bundle.testzip() is None
                assert (
                    len(
                        json.loads(bundle.read("diagnostics/command-runtime-incidents.json"))[
                            "incidents"
                        ]
                    )
                    == 2
                )
            observations.append(
                {
                    "oldTurns": old_turns,
                    "sourceBytes": source_bytes,
                    "readBytes": projection["coverage"]["readBytes"],
                    "sessionBytes": len(body),
                    "zipBytes": len(archive.getvalue()),
                    "readSerializeZipMs": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        # Full-history transport is consumed record by record, not collected into a list.
        tracemalloc.start()
        started = time.perf_counter()
        emitted = 0
        with path.open("rb") as stream:
            for record in stream_prefix(stream, source_bytes, None):
                if record["kind"] == "event":
                    emitted += 1
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert emitted == old_turns + 11
        assert peak < 8 * 1024 * 1024
        streaming = {
            "sourceBytes": source_bytes,
            "eventCount": emitted,
            "peakPythonBytes": peak,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
        }
    result = {
        "ok": True,
        "synthetic": True,
        "observations": observations,
        "fullSessionStream": streaming,
    }
    if args.native or args.native_optimized:
        repo = Path(__file__).resolve().parents[2]
        command = (
            ["powershell", "-NoProfile", "-File", str(repo / "scripts/rust-check/run.ps1")]
            if os.name == "nt"
            else [str(repo / "scripts/rust-check/run.sh")]
        )
        completed = subprocess.run(
            [
                *command,
                *(["--config", "profile.test.opt-level=3"] if args.native_optimized else []),
                "--lib",
                "diagnostics::performance::measure_log_collection_and_archive",
                "--",
                "--ignored",
                "--nocapture",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            print(completed.stdout[-12000:])
            print(completed.stderr[-12000:])
            raise SystemExit(completed.returncode)
        marker = "AF_DIAGNOSTIC_PERF "
        result["nativeLogs"] = json.loads(
            next(
                line.split(marker, 1)[1] for line in completed.stdout.splitlines() if marker in line
            )
        )
        result["nativeLogs"]["optimizedTestProfile"] = args.native_optimized
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered)


if __name__ == "__main__":
    main()
