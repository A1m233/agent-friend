from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "owner-tools" / "run.py"
    spec = importlib.util.spec_from_file_location("owner_tools", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_owner_tools_enable_status_disable(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    target = tmp_path / "owner-capabilities.json"
    monkeypatch.setattr(module, "capability_path", lambda: target)

    assert module.main(["run.py", "enable-memory-inspector"]) == 0
    assert module.read_enabled(target) is True
    assert target.read_bytes().startswith(b"{")
    assert module.main(["run.py", "status"]) == 0
    assert module.main(["run.py", "disable-memory-inspector"]) == 0
    assert module.read_enabled(target) is False
