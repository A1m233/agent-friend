"""Cross-check synthetic desktop results against the actual ZIP and consumer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def verify(root: Path, output: Path | None) -> None:
    repo = Path(__file__).resolve().parents[2]
    source = repo / ".cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py"
    spec = importlib.util.spec_from_file_location("diagnostics_consumer", source)
    assert spec and spec.loader
    consumer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = consumer
    spec.loader.exec_module(consumer)
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] and Path(fixture["root"]).resolve() == root.resolve()
    cases = []
    for action in ("recent", "all", "materials", "cancel"):
        report = json.loads((root / f"desktop-{action}.json").read_text(encoding="utf-8"))
        resources = json.loads((root / f"resources-{action}.json").read_text(encoding="utf-8"))
        assert resources["exitCode"] == 0
        task = report["terminal"]
        record = {
            "action": action,
            "status": task["status"],
            "elapsedMs": task["elapsedMs"],
            "wallMs": report["wallMs"],
            "selection": task["selection"],
            "resources": {
                key: value
                for key, value in resources.items()
                if key not in {"identity", "elapsedMs", "action", "exitCode"}
            },
        }
        if action == "cancel":
            assert task["status"] == "cancelled" and task["result"] is None
            assert report["duplicateRejected"] and report["restoredText"]
            record["cancellationMs"] = report["cancellationMs"]
            record["duplicateRejected"] = True
            record["navigationRestoredSameJob"] = True
            cases.append(record)
            continue
        bundle = Path(task["result"]["path"])
        summary = consumer.inspect_bundle(bundle, verify_crc=True)
        log = next(item for item in summary["logs"] if item["source"] == "runtime-bridge.log")
        assert log["originalBytes"] == fixture["logBytes"]
        if action == "all":
            assert log["readBytes"] == log["originalBytes"] and log["tailStartOffset"] == 0
        else:
            assert log["readBytes"] <= 5 * 1024 * 1024 + 1
        turns = [item["coverage"]["includedTurns"] for item in summary["sessions"]]
        assert turns == ([10] if action == "recent" else [20] * (3 if action == "materials" else 1))
        assert all(item["coverage"]["completeWithinRequestedRange"] for item in summary["sessions"])
        assert summary["installIntegrity"]["status"] == "not_checked"
        with zipfile.ZipFile(bundle) as archive:
            with archive.open("logs/runtime-bridge.log") as stream:
                previous = b""
                while chunk := stream.read(65536):
                    combined = previous + chunk
                    assert b"synthetic-secret" not in combined
                    previous = combined[-64:]
            originals = [
                name
                for name in archive.namelist()
                if name.startswith("attachments/") and not name.endswith("/")
            ]
            assert len(originals) == (2 if action == "materials" else 0)
            hashes = []
            for name in originals:
                digest = hashlib.sha256()
                with archive.open(name) as stream:
                    while chunk := stream.read(65536):
                        digest.update(chunk)
                hashes.append(digest.hexdigest())
            if action == "materials":
                assert sorted(hashes) == sorted(
                    item["assetId"] for item in fixture["largeSessions"]
                )
        record.update(
            {
                "zipBytes": bundle.stat().st_size,
                "runtimeLog": log,
                "includedTurns": turns,
                "verifiedOriginalCount": len(originals),
                "stageMetrics": task["result"]["metrics"]["stages"],
                "crcVerified": True,
                "consumerVerified": True,
                "syntheticCredentialAbsent": True,
            }
        )
        cases.append(record)
    cleanup = json.loads((root / "cleanup-check.json").read_text(encoding="utf-8"))
    assert cleanup["stagedFiles"] == cleanup["archiveParts"] == 0
    paths = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD"], cwd=repo, text=True
        ).splitlines()
    )
    paths.update(
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, text=True
        ).splitlines()
    )
    tracked_inputs = {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
        for name in sorted(paths)
        if name.startswith(
            (
                "agent/src/",
                "agent_bridge/src/",
                "voice_bridge/src/",
                "frontend/src/",
                "frontend/src-tauri/src/",
            )
        )
        and not name.endswith(".test.ts")
    }
    result = {
        "synthetic": True,
        "platform": "Windows 11 x64",
        "configuration": "Tauri dev debug; actual bridge; no chat turns sent",
        "baseCommit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "sourceFilesSha256": tracked_inputs,
        "cases": cases,
        "cleanup": cleanup,
        "limits": [
            "Single local run per scenario, not a latency SLA or a release-build benchmark.",
            "RSS and temporary peaks are sampled; maximum record size was 1 MiB in the multi-session fixture.",
            "Native Common Controls/desktop screenshot observed separately; macOS not verified.",
        ],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps({"ok": True, "caseCount": len(cases), "output": str(output) if output else None})
    )
