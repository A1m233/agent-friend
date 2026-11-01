"""Start or inspect one authorized AF personal-Skill round; never auto-start the next."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:18800"


def api(path: str, payload: object | None = None) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--round", type=int, choices=range(1, 6))
    parser.add_argument("--allow-llm", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not args.prefix or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.prefix
    ):
        parser.error("prefix must be a lowercase evidence name")

    def file(suffix: str) -> Path:
        return root / f"{args.prefix}-{suffix}"

    def save(suffix: str, value: object) -> None:
        with file(suffix).open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)

    if args.command == "status":
        request = json.loads(file("request.json").read_text(encoding="utf-8"))
        run_id = request["runId"]
        snapshot = api(f"/v1/runs/{run_id}")
        events = api(f"/v1/runs/{run_id}/events")
        summary = {
            "status": snapshot["status"],
            "outcome": snapshot.get("outcome"),
            "tool_calls": 0,
            "errors": 0,
        }
        for row in events.get("events", []):
            event = row["event"]
            if event["type"] == "tool_call_request":
                call = event.get("args", {})
                summary["last_command"] = call.get("argv", [event.get("tool_name")])[:5]
            elif event["type"] == "tool_call_result":
                summary["tool_calls"] += 1
                summary["errors"] += bool(event.get("is_error"))
                if event.get("tool_name") == "exec_command":
                    try:
                        output = json.loads(event.get("text", "{}"))
                        game = json.loads(output.get("stdout", "{}"))
                        for key in (
                            "status",
                            "code",
                            "observation_id",
                            "budget_remaining_seconds",
                            "revision",
                        ):
                            if key in game:
                                summary["game_" + key] = game[key]
                        if "visual" in game:
                            summary["boundary"] = game["visual"].get("stop_reason")
                    except (ValueError, TypeError):
                        pass
        print(json.dumps(summary, ensure_ascii=False))
        return
    if not args.allow_llm or not args.template or not args.round:
        parser.error("start requires this round's authorization, template and round number")
    if file("request.json").exists() or (root / "desktop-budget.json").exists():
        parser.error("existing request or budget; inspect it, never resubmit")
    request = json.loads(args.template.read_text(encoding="utf-8-sig"))
    session = request["threadId"]
    runtime = api(f"/v1/sessions/{session}/runtime-snapshot")
    if any(run["status"] != "terminal" for run in runtime["runs"]):
        parser.error("session already has an active run")
    if json.loads((root / "state.json").read_text(encoding="utf-8"))["pending"]:
        parser.error("game operation unresolved")
    run_id = str(uuid4())
    request["runId"] = run_id
    request["messages"] = [
        {
            "id": str(uuid4()),
            "role": "user",
            "content": f"本批第{args.round}/5轮，从当前存档继续独立玩中文版宝可梦火红，最多30分钟。"
            "沿用Astra high、现有窗口和120 FPS。像正常玩家一样，可以自行存读档重试；不查攻略、不读取游戏内部状态、不用金手指。"
            "先读最新版个人pokemon-firered Skill及其记录说明，再读自己的记录并begin。"
            "记录现在由你自行维护，不再自动过期；Skill命令支持结构化argv，按新说明调用。"
            "根据实际需要记笔记、查记录、查看游戏和复用流程，不为测试功能专门绕路。"
            "budget_remaining_seconds为硬截止，剩约60秒finish保存暂停。结束后报告核实的进度、问题和存档，等待下一轮。",
        }
    ]
    paths = [
        *(REPO / "tools/src/tools/pokemon").rglob("*.py"),
        *(REPO / "tools/src/tools/pokemon/skills").rglob("*.md"),
        *(REPO / "agent/src/agent/context").glob("*.py"),
        REPO / "agent/src/agent/command_runtime/tools.py",
        Path(__file__),
    ]
    save("code.json", {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    save("runtime.json", runtime)
    save("request.json", request)
    with file("watch.log").open("xb") as log:
        watcher = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "tools.pokemon",
                "watch-desktop",
                "--session-id",
                session,
                "--max-seconds",
                "1800",
                "--output",
                str(file("watch.json")),
                "--run-id",
                run_id,
                "--runtime-id",
                runtime["runtime"]["bridgeInstanceId"],
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    deadline = time.monotonic() + 40
    while not file("watch.json").exists():
        if watcher.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError("watcher did not arm; no model request submitted")
        time.sleep(0.2)
    # Never automatically retry POST: a lost acknowledgement may still be accepted.
    ack = api("/ag-ui/run?enqueue=true", request)
    save("ack.json", ack)
    print(json.dumps({"run_id": run_id, "watcher_pid": watcher.pid, "ack": ack}))


if __name__ == "__main__":
    main()
