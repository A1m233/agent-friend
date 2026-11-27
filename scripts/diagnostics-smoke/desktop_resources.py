"""Sample a known, task-owned desktop process during the synthetic CDP probe."""

from __future__ import annotations

import json
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import psutil


def run(command: list[str], repo: Path, root: Path, pid: int, action: str) -> int:
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    if fixture.get("synthetic") is not True or Path(fixture["root"]).resolve() != root.resolve():
        raise ValueError("expected an owned synthetic fixture")
    app = psutil.Process(pid)
    # The caller obtains this PID from the matching runtime lease, never by name alone.
    identity = {"pid": pid, "exe": app.exe(), "startedAt": app.create_time()}
    baseline = app.memory_info().rss
    peak = baseline
    peak_children = 0
    temporary_peak = 0
    samples = 0
    started = time.monotonic()
    child = subprocess.Popen(command, cwd=repo)
    while child.poll() is None:
        peak = max(peak, app.memory_info().rss)
        child_rss = 0
        for process in app.children(recursive=True):
            with suppress(psutil.NoSuchProcess):
                child_rss += process.memory_info().rss
        peak_children = max(peak_children, child_rss)
        temporary = 0
        for path in (root / "logs" / "diagnostic-exports").glob("af-export-*.part"):
            with suppress(FileNotFoundError):
                temporary += path.stat().st_size
        temporary_peak = max(temporary_peak, temporary)
        samples += 1
        time.sleep(0.05)
    result = {
        "identity": identity,
        "synthetic": True,
        "action": action,
        "exitCode": child.returncode,
        "sampleCount": samples,
        "samplingIntervalMs": 50,
        "elapsedMs": round((time.monotonic() - started) * 1000, 2),
        "baselineShellRssBytes": baseline,
        "peakShellRssBytes": peak,
        "peakChildRssSumBytes": peak_children,
        "peakStagedFileBytes": temporary_peak,
        "remainingStagedFiles": len(
            list((root / "logs" / "diagnostic-exports").glob("af-export-*.part"))
        ),
        "limitations": "Sampled RSS includes normal desktop activity. Child RSS sums may double-count shared pages. Staging excludes ZIP-in-progress in Downloads and short peaks between samples.",
    }
    (root / f"resources-{action}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return child.returncode
