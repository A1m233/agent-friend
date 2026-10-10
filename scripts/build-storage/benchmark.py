"""Compare recorded inventory with recursive counting using disposable owned fixtures."""

from __future__ import annotations

import platform
import statistics
import tempfile
import time
from pathlib import Path

from build_storage import Storage
from build_storage.files import tree_size


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="agent-friend-storage-benchmark-") as temporary:
        root = Path(temporary)
        repo = root / "source"
        (repo / ".git").mkdir(parents=True)
        storage = Storage(repo, control=root / "control", common=repo / ".git")
        targets = []
        for index in range(4):
            target = root / f"cache-{index}"
            with storage.use(target, "dev"):
                for directory in range(20):
                    parent = target / str(directory)
                    parent.mkdir()
                    for file in range(50):
                        (parent / f"{file}.bin").write_bytes(b"x" * 1024)
            targets.append(target)
        measured = {"recursive": [], "recorded": []}
        for _ in range(7):
            start = time.perf_counter()
            total = sum(tree_size(path) for path in targets)
            measured["recursive"].append(time.perf_counter() - start)
            start = time.perf_counter()
            rows = storage.inventory()["objects"]
            measured["recorded"].append(time.perf_counter() - start)
            if sum(row["bytes"] for row in rows) != total:
                raise RuntimeError("benchmark inventory disagrees with fixture bytes")
        return {
            "schemaVersion": 1,
            "environment": {"platform": platform.platform(), "python": platform.python_version()},
            "fixture": {"objects": 4, "files": 4000, "logicalBytes": total},
            "samplesSeconds": measured,
            "medianSeconds": {key: statistics.median(values) for key, values in measured.items()},
            "scope": "warm filesystem; excludes interpreter startup; no physical-space claim",
        }
