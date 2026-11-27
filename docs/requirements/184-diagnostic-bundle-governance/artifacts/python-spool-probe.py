"""Source of the one-off spool measurement, with local paths replaced by arguments.

Requires the synthetic fixture created by scripts/diagnostics-smoke --desktop-seed
and --desktop-augment. Arguments: fixture directory, output JSON. Does not contact
the running desktop, create conversations, or read non-fixture data.
"""

import hashlib
import json
import sys
import time
from pathlib import Path

from agent.sessions import diagnostic_export as export
from agent.sessions.store import JsonlSessionStore

root = Path(sys.argv[1]).resolve()
fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
assert fixture["synthetic"] and Path(fixture["root"]).resolve() == root
store = JsonlSessionStore(root / "data" / "sessions")
original = export.TemporaryFile
observations = []


class Meter:
    def __init__(self):
        self.inner = original()
        self.peak = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.inner.close()

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def write(self, data):
        written = self.inner.write(data)
        self.peak = max(self.peak, self.inner.tell())
        return written


for limit in (10, None):
    files = []

    def factory():
        meter = Meter()
        files.append(meter)
        return meter

    export.TemporaryFile = factory
    try:
        started = time.perf_counter()
        count = 0
        for record in store.diagnostic_stream(
            fixture["largeSessions"][0]["sessionId"], turn_limit=limit
        ):
            count += record["kind"] == "event"
        assert all(item.inner.closed for item in files)
        observations.append({
            "turnLimit": limit,
            "events": count,
            "peakPythonSpoolLogicalBytes": max(item.peak for item in files),
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "closedAfterExport": True,
        })
    finally:
        export.TemporaryFile = original

result = {
    "synthetic": True,
    "sourceSha256": hashlib.sha256(Path(export.__file__).read_bytes()).hexdigest(),
    "observations": observations,
    "limitations": "Logical spool bytes measured independently, not a total desktop disk peak.",
}
Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
