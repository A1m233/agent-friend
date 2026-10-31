"""Small, shell-independent commands for the installed personal Skill."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .actions import VisualActions
from .client import GameClient
from .protocol import GameError, controller_lock, steps_wire


def action_steps(args: argparse.Namespace) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]]
    if args.steps is not None:
        steps = json.loads(args.steps)
    elif args.tap:
        steps = []
        for key in args.tap:
            steps.extend([{"keys": [key], "frames": 8}, {"keys": [], "frames": 24}])
    else:
        steps = []
        for item in args.step or []:
            keys, separator, frames = item.partition(":")
            if not separator or not frames.isascii() or not frames.isdigit():
                raise GameError("invalid_step", "使用 --step UP:30 或 --step WAIT:24。")
            steps.append({"keys": [] if keys == "WAIT" else keys.split("+"), "frames": int(frames)})
    steps_wire(steps)
    return steps


def execute(client: GameClient, args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command in {"act", "dialogue", "confirm-text", "wait"}:
        supervisor = client.root / "supervisor"
        supervisor.mkdir(exist_ok=True)
        owned = client.supervisor_owned
        with nullcontext() if owned else controller_lock(supervisor):
            client.supervisor_owned = True
            try:
                actions = VisualActions(client)
                if command == "confirm-text":
                    return actions.confirm_text(args.observation_id)
                if command == "dialogue":
                    return actions.dialogue(args.observation_id)
                if command == "wait":
                    return actions.movement(
                        args.observation_id,
                        [{"keys": [], "frames": 12}],
                        "等待可操作画面",
                        wait_only=True,
                    )
                return actions.movement(
                    args.observation_id,
                    action_steps(args),
                    args.expect or "",
                    compare_images=args.compare,
                )
            finally:
                client.supervisor_owned = owned
    if command == "begin":
        client.resume()
        try:
            return client.request("observe")
        except Exception:
            client.pause()
            raise
    if command in {"status", "pause", "resume"}:
        simple_result: dict[str, Any] = getattr(client, command)()
        return simple_result
    if command in {"checkpoint", "finish"}:
        try:
            observation = args.observation_id or client.status().get("observation_id")
            if observation is None:
                observation = client.request("observe")["observation_id"]
            result = client.request("checkpoint", observation_id=observation)
        finally:
            if command == "finish":
                client.pause()
        if command == "finish":
            result["paused"] = True
        return result
    return client.request(
        command,
        observation_id=args.observation_id,
        steps=action_steps(args) if command == "act" else None,
    )


def main(binding: Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AF 火红个人 Skill 控制器")
    parser.add_argument(
        "command",
        choices=[
            "status",
            "begin",
            "observe",
            "act",
            "dialogue",
            "confirm-text",
            "wait",
            "checkpoint",
            "finish",
            "pause",
            "resume",
        ],
    )
    parser.add_argument("--instance", type=Path)
    parser.add_argument("--observation-id")
    parser.add_argument("--expect", help="本批动作的预期可见变化，最多 200 字；不等于已发生。")
    parser.add_argument("--compare", action="store_true", help="方向操作显式附带操作前画面。")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--tap", action="append", help="按住 8 帧再松开 24 帧；可重复，如 --tap A --tap A。"
    )
    actions.add_argument(
        "--step", action="append", help="有序按键段，如 --step UP:30 --step WAIT:24。"
    )
    actions.add_argument("--steps", help="兼容 JSON 数组；Skill 绑定命令优先用 --tap / --step。")
    args = parser.parse_args(argv)
    try:
        if args.compare and args.command != "act":
            raise GameError("invalid_arguments", "--compare 仅用于 act。")
        if args.expect is not None and (args.command != "act" or len(args.expect) > 200):
            raise GameError("invalid_expectation", "--expect 仅用于 act，最多 200 字。")
        if args.command != "act" and any((args.tap, args.step, args.steps is not None)):
            raise GameError("invalid_arguments", "按键参数仅用于 act。")
        if args.command in {"act", "dialogue", "confirm-text", "wait"}:
            if not args.observation_id:
                raise GameError(
                    "missing_observation",
                    "act 需要 --observation-id，使用最近一次实际看过的截图编号。",
                )
            if args.command == "act":
                action_steps(args)
        root = args.instance
        if root is None:
            if not binding.is_file():
                raise GameError("not_prepared", "尚未绑定实例；请先准备游戏资源。")
            root = Path(json.loads(binding.read_text(encoding="utf-8"))["instance_root"])
        client = GameClient(root)
        budget_path = root / "desktop-budget.json"
        budget = (
            json.loads(budget_path.read_text(encoding="utf-8")) if budget_path.exists() else None
        )
        if (
            budget
            and args.command in {"begin", "resume", "act", "dialogue", "confirm-text", "wait"}
            and time.time() >= budget["deadline"]
        ):
            client.pause()
            raise GameError(
                "budget_exhausted", "本轮试玩时限已到，已暂停；可 finish 保存，不能继续按键。"
            )
        result = execute(client, args)
        if result.get("screenshot") and "image_paths" not in result:
            result["image_paths"] = [Path(result["screenshot"]).name]
        if budget:
            result["budget_remaining_seconds"] = max(0, int(budget["deadline"] - time.time()))
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("status") in {"failed", "cancelled"} else 0
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
