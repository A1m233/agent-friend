"""Synchronize the project memory-maintenance Skill with its bundled AF copy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.skills import SkillLoader, SkillRoot
from agent.skills.first_party_updates.store import PRODUCT_REPOSITORY, package_snapshot_digest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib.builtin_skill_snapshot import replace_builtin_snapshot

NAME = "maintain-af-memory"
SOURCE = Path(".cursor/skills") / NAME
ASSETS = Path("agent/src/agent/skills/builtin_assets")


def update(repo: Path, *, sync: bool) -> str:
    source = repo / SOURCE
    assets = repo / ASSETS
    target = assets / NAME
    for path in (source, assets, target, assets / "manifest.json"):
        if path.resolve() != path:
            raise ValueError(f"Expected a physical repository path: {path}")

    if not (source / "SKILL.md").is_file():
        raise ValueError(f"Missing project Skill entry: {source / 'SKILL.md'}")
    digest = package_snapshot_digest(source)
    catalog = SkillLoader().load((SkillRoot("builtin", source),))
    if [record.name for record in catalog.entries] != [NAME] or catalog.issues:
        raise ValueError(f"Invalid {NAME} project Skill: {catalog.issues}")

    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or not isinstance(manifest.get("skills"), dict):
        raise ValueError("Unsupported builtin Skill manifest")
    entry = {
        "source_repository": PRODUCT_REPOSITORY,
        "source_path": SOURCE.as_posix(),
        "snapshot_sha256": digest,
    }
    stored = manifest["skills"].get(NAME)
    matches = target.is_dir() and package_snapshot_digest(target) == digest
    if stored != entry or not matches:
        if not sync:
            raise ValueError(f"Bundled {NAME} differs from its project source; run sync-builtin")
        replace_builtin_snapshot(assets, NAME, source, entry)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("sync-builtin", "verify-builtin"))
    args = parser.parse_args()
    try:
        digest = update(Path(__file__).resolve().parents[2], sync=args.mode == "sync-builtin")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"{NAME} {digest}")


if __name__ == "__main__":
    main()
