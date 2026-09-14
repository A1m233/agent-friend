"""Deterministic diagnostic range benchmark; synthetic data only, no network."""

from __future__ import annotations

import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

from agent.command_runtime.sandbox_evidence import SandboxEvidence
from agent.sessions.diagnostic_export import export_prefix


def main() -> None:
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
    print(json.dumps({"ok": True, "synthetic": True, "observations": observations}, indent=2))


if __name__ == "__main__":
    main()
