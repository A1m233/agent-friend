from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "scripts" / "showcase-snapshot" / "snapshot.py"


class SnapshotModule(Protocol):
    def is_allowed(self, path: str) -> bool: ...


def load_snapshot_module() -> SnapshotModule:
    spec = importlib.util.spec_from_file_location("showcase_snapshot_for_tests", SNAPSHOT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(SnapshotModule, module)


snapshot = load_snapshot_module()


def test_frontend_root_html_entries_are_allowed_by_pattern() -> None:
    assert snapshot.is_allowed("frontend/voice-call.html")
    assert snapshot.is_allowed("frontend/future-window.html")
    assert not snapshot.is_allowed("frontend/nested/future-window.html")
