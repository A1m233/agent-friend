"""Repository operation entry for the optional Pokémon personal Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent.paths import skills_dir, user_data_dir

from .runtime.client import GameClient
from .runtime.protocol import GameError
from .setup import install, prepare, upgrade


def main() -> int:
    parser = argparse.ArgumentParser(description="AF Pokémon personal Skill experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install")
    sub.add_parser("update")
    p = sub.add_parser("watch-desktop")
    p.add_argument("--session-id", required=True)
    p.add_argument("--max-seconds", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("ocr-check")
    p.add_argument("--samples", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--rom", type=Path, required=True)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument(
        "--source", required=True, help="资源来源，用于可追溯记录，不验证授权或中文兼容性。"
    )
    p.add_argument("--language", choices=["zh-CN", "homebrew-test"], required=True)
    sub.add_parser("launch")
    sub.add_parser("test")
    p = sub.add_parser("verify-visual")
    p.add_argument("--instance", type=Path)
    p.add_argument(
        "--movement", action="store_true", help="还测试一次向右短步和无按键等待，最后恢复。"
    )
    p = sub.add_parser("verify-emulator")
    p.add_argument("--instance", type=Path, required=True)
    p = sub.add_parser("refresh-test-adapter")
    p.add_argument("--instance", type=Path, required=True)
    p = sub.add_parser("emulator-prepare")
    p.add_argument("--emulator", type=Path)
    p = sub.add_parser("fetch-candidate")
    p.add_argument("--url", required=True)
    p.add_argument("--source-page", required=True)
    p = sub.add_parser("recover")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--reason", required=True)
    for name in ("play", "continue-play"):
        p = sub.add_parser(name)
        p.add_argument("--model", required=True)
        p.add_argument("--persona", default="default")
        p.add_argument("--reasoning-preset")
        p.add_argument("--max-calls", type=int, default=20)
        p.add_argument("--max-seconds", type=int, default=600)
        p.add_argument("--allow-llm", action="store_true")
    for name in ("status", "observe", "act", "checkpoint", "pause", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--instance", type=Path)
        if name in {"act", "checkpoint"}:
            p.add_argument("--observation-id", required=True)
        if name == "act":
            p.add_argument("--steps", required=True)
    args = parser.parse_args()
    skill_path = skills_dir() / "pokemon-firered"
    result: dict[str, Any]
    try:
        if args.command == "install":
            result = install(skills_dir())
        elif args.command == "update":
            result = upgrade(skills_dir(), user_data_dir() / "pokemon" / "skill-backups")
        elif args.command == "test":
            return subprocess.run(
                [sys.executable, "-m", "pytest", "tools/tests/pokemon", "-q"], check=False
            ).returncode
        elif args.command == "emulator-prepare":
            smoke = Path(__file__).resolve().parents[4] / "scripts/pokemon/emulator_smoke.py"
            argv = [sys.executable, str(smoke)]
            if args.emulator:
                argv.extend(["--emulator", str(args.emulator)])
            return subprocess.run(argv, check=False).returncode
        elif args.command == "fetch-candidate":
            fetch = Path(__file__).resolve().parents[4] / "scripts/pokemon/fetch_candidate.py"
            return subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(fetch),
                    "--url",
                    args.url,
                    "--source-page",
                    args.source_page,
                ],
                check=False,
            ).returncode
        elif args.command == "doctor":
            result = {
                "skill_path": str(skill_path),
                "installed": (skill_path / "SKILL.md").is_file(),
                "instance_bound": (skill_path / "binding.json").is_file(),
                "python": sys.executable,
                "model_called": False,
                "mode": "personal_external",
                "builtin": False,
            }
        elif args.command == "ocr-check":
            script = Path(__file__).resolve().parents[4] / "scripts/pokemon/ocr_check.py"
            return subprocess.run(
                [
                    "uv",
                    "run",
                    "--no-project",
                    "--with",
                    "rapidocr-onnxruntime==1.4.4",
                    "--with",
                    "pillow",
                    "python",
                    "-X",
                    "utf8",
                    str(script),
                    "--samples",
                    str(args.samples.resolve()),
                    "--output",
                    str(args.output.resolve()),
                ],
                check=False,
            ).returncode
        elif args.command == "prepare":
            result = prepare(
                rom=args.rom,
                emulator=args.emulator,
                instances_root=user_data_dir() / "pokemon" / "instances",
                skill_path=skill_path,
                source=args.source,
                language=args.language,
            )
        else:
            root = getattr(args, "instance", None)
            if root is None:
                binding = json.loads((skill_path / "binding.json").read_text(encoding="utf-8"))
                root = Path(binding["instance_root"])
            client = GameClient(root)
            if args.command == "watch-desktop":
                from .desktop_pilot import watch

                result = watch(
                    root,
                    session_id=args.session_id,
                    max_seconds=args.max_seconds,
                    output=args.output,
                )
            elif args.command == "refresh-test-adapter":
                from .emulator_check import refresh_adapter

                result = refresh_adapter(root)
            elif args.command == "recover":
                from .recovery import recover

                result = recover(root, args.checkpoint, args.reason)
            elif args.command == "verify-visual":
                from .visual_check import verify as verify_visual

                result = verify_visual(root, movement=args.movement)
            elif args.command == "verify-emulator":
                from .emulator_check import verify

                result = verify(root)
            elif args.command in {"play", "continue-play"}:
                from .runner import play

                result = play(
                    root,
                    skill_path,
                    model=args.model,
                    persona=args.persona,
                    max_calls=args.max_calls,
                    max_seconds=args.max_seconds,
                    allow_llm=args.allow_llm,
                    reasoning_preset=args.reasoning_preset,
                    continue_existing=args.command == "continue-play",
                )
            elif args.command == "launch":
                data = (root / "game.gba").read_bytes()
                if hashlib.sha256(data).hexdigest() != client.config["rom_sha256"]:
                    raise GameError("rom_changed", "游戏文件已变化，拒绝加载旧实例。")
                # Print exact invocation; launch ownership and script-option support are
                # verified by the emulator smoke before enabling unattended process start.
                result = {
                    "status": "manual_launch_required",
                    "emulator": client.config["emulator_path"],
                    "rom": str(root / "game.gba"),
                    "lua_script": str(root / "bridge.lua"),
                    "message": "在 mGBA 中打开此 ROM，通过 Tools > Scripting 加载此 Lua。不要重复加载脚本。",
                }
            elif args.command in {"status", "pause", "resume"}:
                result = getattr(client, args.command)()
            else:
                result = client.request(
                    args.command,
                    observation_id=getattr(args, "observation_id", None),
                    steps=json.loads(args.steps) if args.command == "act" else None,
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GameError, OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": getattr(exc, "code", "invalid_setup"),
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
