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
from .knowledge import Knowledge, notes
from .ocr import read_text
from .protocol import GameError, controller_lock, steps_wire
from .saves import list_saves, load_save
from .workflows import replay, save_workflow, trace_action

WORKFLOW_COMMANDS = {
    "inspect": "inspect",
    "use-item": "item",
    "switch": "switch",
    "move": "move",
    "route": "route",
}
GAMEPLAY_COMMANDS = {"act", "dialogue", "confirm-text", "wait", "load", *WORKFLOW_COMMANDS}


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
    if command == "saves":
        return list_saves(client.root)
    if command == "notes":
        return notes(client.root, args.text)
    if command == "knowledge":
        knowledge = Knowledge(client.root)
        if args.data is None and args.text is None:
            return knowledge.query(
                args.section, args.entity, details=args.details, all_records=args.all_records
            )
        return knowledge.update(
            json.loads(args.data) if args.data is not None else {},
            args.source,
            certainty=args.certainty,
            revision=args.revision,
            text=args.text,
        )
    if command == "workflow-save":
        if not args.data:
            raise GameError("missing_workflow", "使用 --data 提供已实际走通的流程。")
        return save_workflow(client.root, json.loads(args.data))
    if command == "ocr":
        if not args.source or len(args.source) != 1:
            raise GameError("missing_evidence", "OCR 需要 --source 截图编号，不接受 ROM/存档路径。")
        return read_text(client.root, args.source[0])
    if command in GAMEPLAY_COMMANDS:
        supervisor = client.root / "supervisor"
        supervisor.mkdir(exist_ok=True)
        owned = client.supervisor_owned
        with nullcontext() if owned else controller_lock(supervisor):
            client.supervisor_owned = True
            try:
                if command == "load":
                    return load_save(client, args.checkpoint_id)
                if command in WORKFLOW_COMMANDS:
                    return replay(
                        client, args.name, args.observation_id, WORKFLOW_COMMANDS[command]
                    )
                actions = VisualActions(client)
                if command == "confirm-text":
                    return actions.confirm_text(args.observation_id)
                if command == "dialogue":
                    return actions.dialogue(args.observation_id)
                if command == "wait":
                    result = actions.movement(
                        args.observation_id,
                        [{"keys": [], "frames": 12}],
                        "等待可操作画面",
                        wait_only=True,
                    )
                    trace_action(
                        client.root, args.observation_id, result, [{"keys": [], "frames": 12}]
                    )
                    return result
                result = actions.movement(
                    args.observation_id,
                    action_steps(args),
                    args.expect or "",
                    compare_images=args.compare,
                )
                trace_action(client.root, args.observation_id, result, action_steps(args))
                return result
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
        result["checkpoint_id"] = result["operation_id"]
        return result
    return client.request(
        command,
        observation_id=args.observation_id,
        steps=action_steps(args) if command == "act" else None,
    )


def main(binding: Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AF 火红个人 Skill 控制器",
        epilog=(
            "记录写入格式（值仅为示例）：knowledge --source <截图编号> "
            '--data \'{"party":{"bud":{"hp":"39/39"}}}\'。'
            "Skill 使用结构化 argv，把 JSON 作为一个参数，不再经过 shell 转义。"
            "可同时 --text 整页替换当前计划；默认查询当前页、队伍/背包与经验索引。"
            "--section/--entity 是查询过滤器，写入 JSON 仍须包含 section/entity 两层。"
        ),
    )
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
            "notes",
            "knowledge",
            "workflow-save",
            "ocr",
            "saves",
            "load",
            *WORKFLOW_COMMANDS,
        ],
    )
    parser.add_argument("--instance", type=Path)
    parser.add_argument("--observation-id")
    parser.add_argument(
        "--checkpoint-id",
        help="仅 load：checkpoint/finish 或 saves 返回的存档编号，不是普通截图编号。",
    )
    parser.add_argument(
        "--section", choices=["party", "bag", "goals", "places", "notes"], help="仅过滤查询。"
    )
    parser.add_argument("--entity", help="仅过滤查询：稳定英文实体名，如 bud；不用队伍槽位。")
    parser.add_argument(
        "--all",
        dest="all_records",
        action="store_true",
        default=None,
        help="仅 knowledge 查询：展开全部旧记录；通常按 section/entity 查阅。",
    )
    parser.add_argument(
        "--data",
        help="knowledge 的 section→entity→字段 JSON（见下方示例），或 workflow-save 的已验证流程 JSON。",
    )
    parser.add_argument(
        "--source", action="append", help="可选记录来源截图，可重复；OCR 必须且仅指定一张。"
    )
    parser.add_argument(
        "--details",
        action="store_true",
        default=None,
        help="仅 knowledge 查询：包含字段来源和更新时间。",
    )
    parser.add_argument("--certainty", choices=["observed", "inferred"], default="observed")
    parser.add_argument("--revision", type=int, help="可选：query 返回的记录版本，冲突时拒绝覆盖。")
    parser.add_argument(
        "--text", help="knowledge/notes：整页替换当前页，最多 8000 字；可与 knowledge --data 合并。"
    )
    parser.add_argument("--name", help="knowledge 返回的已保存 workflow ID。")
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
        allowed = {
            "section": {"knowledge"},
            "details": {"knowledge"},
            "all_records": {"knowledge"},
            "entity": {"knowledge"},
            "revision": {"knowledge"},
            "data": {"knowledge", "workflow-save"},
            "source": {"knowledge", "ocr"},
            "text": {"notes", "knowledge"},
            "name": set(WORKFLOW_COMMANDS),
            "checkpoint_id": {"load"},
        }
        for field, commands in allowed.items():
            if getattr(args, field) is not None and args.command not in commands:
                raise GameError("invalid_arguments", f"--{field} 不适用于 {args.command}。")
        if args.command == "knowledge":
            writing = args.data is not None or args.text is not None
            if writing and any((args.section, args.entity, args.details, args.all_records)):
                raise GameError(
                    "invalid_arguments", "写入不能同时传 section/entity/details/all 查询参数。"
                )
            if not writing and (args.source or args.revision is not None):
                raise GameError(
                    "invalid_arguments", "source/revision 仅用于写入；查询不需要截图或版本。"
                )
        if args.command in WORKFLOW_COMMANDS and not args.name:
            raise GameError(
                "missing_workflow", "需要 --name 已录制的 workflow ID；先 knowledge 查询。"
            )
        if args.compare and args.command != "act":
            raise GameError("invalid_arguments", "--compare 仅用于 act。")
        if args.expect is not None and (args.command != "act" or len(args.expect) > 200):
            raise GameError("invalid_expectation", "--expect 仅用于 act，最多 200 字。")
        if args.command != "act" and any((args.tap, args.step, args.steps is not None)):
            raise GameError("invalid_arguments", "按键参数仅用于 act。")
        if args.command == "load" and not args.checkpoint_id:
            raise GameError("missing_checkpoint", "load 需要 --checkpoint-id；先 saves 查询。")
        if args.command in GAMEPLAY_COMMANDS - {"load"}:
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
            not budget
            and (root / "desktop-watch-required.json").exists()
            and args.command in {"begin", "resume", *GAMEPLAY_COMMANDS}
        ):
            client.pause()
            raise GameError(
                "budget_missing", "本实例需要宿主重新布防试玩预算；保持暂停，不执行按键。"
            )
        if (
            budget
            and args.command in {"begin", "resume", *GAMEPLAY_COMMANDS}
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
