from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest
from agent.skills.first_party_updates.store import package_snapshot_digest


def _tool() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "codex-adapters" / "builtin_skill.py"
    spec = importlib.util.spec_from_file_location("project_builtin_skill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _tool()


def test_checked_in_project_skill_matches_its_packaged_snapshot() -> None:
    tool.update(Path(__file__).resolve().parents[2], sync=False)


def _fixture(root: Path) -> tuple[Path, Path]:
    source = root / tool.SOURCE
    assets = root / tool.ASSETS
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {tool.NAME}\ndescription: Test a local skill.\n---\n\nExample.\n",
        encoding="utf-8",
    )
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_text(
        '{"schema_version": 2, "skills": {"unrelated": {"source_path":"example"}}}\n',
        encoding="utf-8",
    )
    return source, assets


def test_sync_and_verify_include_changed_removed_and_extra_resources(tmp_path: Path) -> None:
    source, assets = _fixture(tmp_path)
    old = source / "references" / "old.md"
    old.write_text("Old reference\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sync-builtin"):
        tool.update(tmp_path, sync=False)
    digest = tool.update(tmp_path, sync=True)
    target = assets / tool.NAME
    assert tool.update(tmp_path, sync=False) == digest == package_snapshot_digest(target)
    assert (target / "references" / "old.md").read_bytes() == old.read_bytes()

    old.rename(old.with_name("current.md"))
    (target / "extra.md").write_text("Only in the mirror", encoding="utf-8")
    with pytest.raises(ValueError, match="sync-builtin"):
        tool.update(tmp_path, sync=False)
    replacement = tool.update(tmp_path, sync=True)
    assert replacement != digest and replacement == tool.update(tmp_path, sync=False)
    assert not (target / "extra.md").exists()
    assert not (target / "references" / "old.md").exists()
    assert (target / "references" / "current.md").read_bytes() == old.with_name(
        "current.md"
    ).read_bytes()

    manifest_path = assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skills"]["unrelated"] == {"source_path": "example"}
    manifest["skills"][tool.NAME]["snapshot_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="sync-builtin"):
        tool.update(tmp_path, sync=False)


def test_concurrent_skill_publications_merge_only_their_own_entries(tmp_path: Path) -> None:
    local, assets = _fixture(tmp_path)
    old_manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    guide = tmp_path / "guide"
    guide.mkdir()
    (guide / "SKILL.md").write_text("test guide", encoding="utf-8")
    records = {
        "agent-friend-guide": (guide, {"snapshot_sha256": package_snapshot_digest(guide)}),
        tool.NAME: (local, {"snapshot_sha256": package_snapshot_digest(local)}),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(tool.replace_builtin_snapshot, assets, name, source, entry)
            for name, (source, entry) in records.items()
        ]
        for future in futures:
            future.result()
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == old_manifest["skills"] | {
        name: entry for name, (_, entry) in records.items()
    }
    for name, (_, entry) in records.items():
        assert package_snapshot_digest(assets / name) == entry["snapshot_sha256"]
