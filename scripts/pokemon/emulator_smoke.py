"""Prepare an original tiny GBA program and pinned mGBA for zero-LLM checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO / "scripts/_lib"))

from build_storage import Storage  # noqa: E402
from tools.pokemon.setup import install, prepare, source_files  # noqa: E402


def homebrew() -> bytes:
    """Original ARM7 program: red display; pixel (0,0) green while A is held."""
    code: list[int] = []
    literals: list[tuple[int, int, int]] = []

    def load(register: int, value: int) -> None:
        literals.append((len(code), register, value))
        code.append(0)

    load(0, 0x04000000)
    load(1, 0x0403)  # mode 3, BG2 enabled
    code.append(0xE1C010B0)  # strh r1,[r0]
    load(0, 0x06000000)
    code.append(0xE3A0101F)  # red
    load(2, 240 * 160)
    code.extend([0xE0C010B2, 0xE2522001, 0x1AFFFFFC])
    loop = len(code)
    load(0, 0x04000130)
    code.extend([0xE1D010B0, 0xE2011001, 0xE3510000])
    load(2, 0x03E0)
    code.extend([0x01A01002, 0x13A0101F])
    load(0, 0x06000000)
    code.append(0xE1C010B0)
    code.append(0xEA000000 | ((loop - len(code) - 2) & 0xFFFFFF))
    for index, register, value in literals:
        offset = (len(code) - index - 2) * 4
        code[index] = 0xE59F0000 | (register << 12) | offset
        code.append(value)
    rom = bytearray(65536)
    struct.pack_into("<I", rom, 0, 0xEA00002E)
    rom[0xA0:0xAC] = b"AF TEST     "
    rom[0xAC:0xB0] = b"AFTE"
    rom[0xB2] = 0x96
    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 255
    struct.pack_into(f"<{len(code)}I", rom, 0xC0, *code)
    return bytes(rom)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator", type=Path)
    args = parser.parse_args()
    storage = Storage(REPO)
    root = storage.target("runtime-regression") / "pokemon"
    with storage.use(
        root, "runtime-regression", purpose="pokemon zero-LLM emulator preparation", retain=True
    ):
        emulator = args.emulator
        if emulator is None:
            if os.name != "nt":
                raise SystemExit("Provide --emulator pointing to mGBA 0.10.5 on this platform.")
            url = "https://github.com/mgba-emu/mgba/releases/download/0.10.5/mGBA-0.10.5-win64.7z"
            archive = root / "mGBA-0.10.5-win64.7z"
            if not archive.exists():
                with urllib.request.urlopen(url, timeout=60) as response:
                    archive.write_bytes(response.read(50 * 1024 * 1024))
            binary = root / "emulator"
            binary.mkdir(exist_ok=True)
            if not list(binary.rglob("mGBA.exe")):
                members = subprocess.check_output(
                    ["tar", "-tf", str(archive)], text=True
                ).splitlines()
                for member in members:
                    if Path(member).is_absolute() or ".." in Path(member).parts or ":" in member:
                        raise SystemExit("Unsafe archive path")
                subprocess.run(["tar", "-xf", str(archive), "-C", str(binary)], check=True)
            candidates = list(binary.rglob("mGBA.exe"))
            if len(candidates) != 1:
                raise SystemExit("Expected one mGBA executable")
            emulator = candidates[0]
            (root / "download.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "checksum_source": "locally_measured_not_publisher_supplied",
                    }
                ),
                encoding="utf-8",
            )
        rom = root / "af-homebrew.gba"
        rom.write_bytes(homebrew())
        source_digest = hashlib.sha256(
            b"".join(name.encode() + data for name, data in sorted(source_files().items()))
        ).hexdigest()
        personal = root / f"isolated-skills-{source_digest[:12]}"
        install(personal)
        skill = personal / "pokemon-firered"
        if (skill / "binding.json").exists():
            result = json.loads((skill / "binding.json").read_text(encoding="utf-8"))
        else:
            result = prepare(
                rom=rom,
                emulator=emulator,
                instances_root=root / "instances",
                skill_path=skill,
                source="Original AF smoke program; no Nintendo assets",
                language="homebrew-test",
            )
        result["emulator"] = str(emulator)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
