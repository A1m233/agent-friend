"""Prepare optional local assets without changing the desktop's builtin catalog."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zlib
from pathlib import Path
from uuid import uuid4

from .runtime.protocol import GameError, atomic_json, atomic_text

SKILL_ROOT = Path(__file__).parent / "skills" / "pokemon-firered"
RUNTIME_ROOT = Path(__file__).parent / "runtime"


def source_files() -> dict[str, bytes]:
    files = {
        p.relative_to(SKILL_ROOT).as_posix(): p.read_bytes()
        for p in SKILL_ROOT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    files.update(
        {
            f"scripts/pokemon_runtime/{p.name}": p.read_bytes()
            for p in RUNTIME_ROOT.iterdir()
            if p.is_file() and p.suffix in {".py", ".lua"}
        }
    )
    return files


def install(personal_root: Path) -> dict[str, object]:
    target = personal_root / "pokemon-firered"
    files = source_files()
    if target.exists():
        if all(
            (target / name).is_file() and (target / name).read_bytes() == data
            for name, data in files.items()
        ):
            return {"status": "unchanged", "skill_path": str(target)}
        raise GameError("skill_exists", "个人 Skill 已有不同内容；保留原文件，先核对差异再更新。")
    personal_root.mkdir(parents=True, exist_ok=True)
    stage = personal_root / f".pokemon-stage-{uuid4().hex}"
    stage.mkdir()
    try:
        for name, data in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        atomic_json(
            stage / "installation.json",
            {
                "schema": 1,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                "python_executable": sys.executable,
            },
        )
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"status": "installed", "skill_path": str(target)}


def upgrade(personal_root: Path, backups_root: Path) -> dict[str, object]:
    """Replace only a verified managed package; retain binding and user resources."""
    target = personal_root / "pokemon-firered"
    if not target.exists():
        return install(personal_root)
    files = source_files()
    manifest = json.loads((target / "installation.json").read_text(encoding="utf-8"))
    managed = manifest["files"]
    for name, digest in managed.items():
        path = target / name
        if not path.resolve().is_relative_to(target.resolve()) or not path.is_file():
            raise GameError("modified_skill", "已安装包的文件清单无效；保留现状。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise GameError("modified_skill", f"个人文件已修改，不能自动覆盖：{name}")
    for name in files.keys() - managed.keys():
        if (target / name).exists():
            raise GameError("modified_skill", f"新资源与个人文件冲突：{name}")
    if files.keys() == managed.keys() and all(
        (target / n).read_bytes() == b for n, b in files.items()
    ):
        return {"status": "unchanged", "skill_path": str(target)}
    stage = personal_root / f".pokemon-stage-{uuid4().hex}"
    backups_root.mkdir(parents=True, exist_ok=True)
    backup = backups_root / uuid4().hex
    try:
        shutil.copytree(target, stage)
        for name in managed.keys() - files.keys():
            (stage / name).unlink()
        for name, data in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        atomic_json(
            stage / "installation.json",
            {
                "schema": 1,
                "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
                "python_executable": sys.executable,
            },
        )
        target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            backup.rename(target)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"status": "updated", "skill_path": str(target), "backup": str(backup)}


def prepare(
    *, rom: Path, emulator: Path, instances_root: Path, skill_path: Path, source: str, language: str
) -> dict[str, object]:
    rom, emulator = rom.resolve(strict=True), emulator.resolve(strict=True)
    if (
        not rom.is_file()
        or rom.suffix.lower() != ".gba"
        or not 192 <= rom.stat().st_size <= 32 * 1024 * 1024
    ):
        raise GameError("invalid_rom", "需要 192 字节至 32 MiB 的 GBA 文件。")
    if not emulator.is_file():
        raise GameError("invalid_emulator", "需要已有的 mGBA 0.10.5 可执行文件。")
    if not (skill_path / "SKILL.md").is_file():
        raise GameError("skill_missing", "先 install 个人 Skill。")
    if (skill_path / "binding.json").exists():
        raise GameError("already_bound", "Skill 已绑定实例；不覆盖已有游戏。先核对当前实例状态。")
    data = rom.read_bytes()
    instance_id = uuid4().hex
    root = instances_root.resolve() / instance_id
    root.mkdir(parents=True, exist_ok=False)
    for child in ("operations", "screens", "receipts", "saves", "claims"):
        (root / child).mkdir()
    # Copy to isolate battery saves from the user's original ROM and other emulators.
    (root / "game.gba").write_bytes(data)
    config = {
        "schema": 1,
        "instance_id": instance_id,
        "rom_sha256": hashlib.sha256(data).hexdigest(),
        "rom_crc32": f"{zlib.crc32(data):08x}",
        "rom_source": source,
        "language_claim": language,
        "language_verified": False,
        "emulator_path": str(emulator),
        "emulator_version": "0.10.5",
    }
    atomic_json(root / "instance.json", config)
    atomic_json(root / "state.json", {"schema": 1, "pending": None, "observation_id": None})
    atomic_text(root / "paused", "prepared_requires_explicit_resume\n")
    prelude = f'AF_ROOT = {json.dumps(root.as_posix(), ensure_ascii=False)}\nAF_ROM_CRC32 = "{config["rom_crc32"]}"\n'
    script = (skill_path / "scripts/pokemon_runtime/bridge.lua").read_text(encoding="utf-8")
    atomic_text(root / "bridge.lua", prelude + script)
    atomic_json(skill_path / "binding.json", {"schema": 1, "instance_root": str(root)})
    return {
        "status": "prepared_paused",
        "instance_root": str(root),
        **config,
        "script": str(root / "bridge.lua"),
        "rom": str(root / "game.gba"),
    }
