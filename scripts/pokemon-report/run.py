"""Read-only desktop run inspection; never starts a run or calls a model."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def parsed(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument(
        "--saved",
        action="store_true",
        help="只分析已保存的终态 snapshot/events，不依赖短期API保留。",
    )
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.prefix):
        raise SystemExit("invalid evidence prefix")
    import uuid

    run_id = str(uuid.UUID(args.run_id))

    def api(suffix: str) -> dict[str, Any]:
        with urllib.request.urlopen(
            f"http://127.0.0.1:18800/v1/runs/{run_id}{suffix}", timeout=10
        ) as response:
            return json.load(response)

    def write(suffix: str, value: object) -> None:
        path = root / f"{args.prefix}-{suffix}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.saved:
        snapshot = json.loads((root / f"{args.prefix}-snapshot.json").read_text(encoding="utf-8"))
        journal = json.loads((root / f"{args.prefix}-events.json").read_text(encoding="utf-8"))
        if snapshot.get("run_id") != run_id or snapshot.get("status") != "terminal":
            raise SystemExit("saved snapshot must be the requested terminal run")
    else:
        snapshot, journal = api(""), api("/events")
    write("snapshot", snapshot)
    write("events", journal)
    requests = {}
    results = []
    texts: dict[str, str] = {}
    artifacts: set[Path] = set()
    for row in journal["events"]:
        event = row["event"]
        if event["type"] == "tool_call_request":
            requests[event["tool_call_id"]] = event["args"]
        elif event["type"] == "text_delta":
            key = event.get("message_id", "default")
            texts[key] = texts.get(key, "") + event.get("text", "")
        elif event["type"] == "tool_call_result":
            output = parsed(event.get("text", ""))
            game = parsed(output.get("stdout", ""))
            request = requests.get(event["tool_call_id"], {})
            cmd = request.get("cmd", "")
            argv = request.get("argv", [])
            command = (
                cmd.split("scripts/game.py ", 1)[-1].split(" ", 1)[0] if cmd else event["tool_name"]
            )
            if isinstance(argv, list):
                entry = next(
                    (
                        i
                        for i, arg in enumerate(argv)
                        if str(arg).replace("\\", "/").endswith("scripts/game.py")
                    ),
                    None,
                )
                if entry is not None and entry + 1 < len(argv):
                    command = argv[entry + 1]
            results.append(
                {
                    "index": len(results) + 1,
                    "cmd": cmd,
                    "argv": argv,
                    "command": command,
                    "error": event.get("is_error"),
                    "code": event.get("error_code"),
                    "seconds": event.get("duration_seconds"),
                    "read_access_seconds": output.get("launch_timings", {}).get(
                        "read_access_seconds"
                    ),
                    "game": game,
                    "stderr": output.get("stderr", "")[:2000],
                }
            )
            for name in game.get("image_paths", []):
                path = (root / "screens" / name).resolve()
                if path.is_relative_to(root) and path.is_file():
                    artifacts.add(path)
            if game.get("save_path"):
                path = Path(game["save_path"]).resolve()
                if path.is_relative_to(root) and path.is_file():
                    artifacts.add(path)
    summary = {
        "evidence_source": "saved_terminal" if args.saved else "live_api",
        "code_check_scope": "current_checkout_at_report_time",
        "snapshot": snapshot,
        "tool_count": len(results),
        "errors": [r for r in results if r["error"] or r["game"].get("status") == "failed"],
        "commands": dict(collections.Counter(r["command"] for r in results)),
        "assistant_messages": list(texts.values()),
        "tools": results,
    }
    if snapshot["status"] == "terminal":
        from agent.paths import log_dir

        latency_path = root / f"{args.prefix}-latency.log"
        if not latency_path.exists():
            logs = sorted(log_dir().glob("agent_bridge.log*"))[:6]
            if logs:
                captured = subprocess.run(
                    [
                        "rg",
                        "--no-filename",
                        "--fixed-strings",
                        f"run_id={run_id} ",
                        *map(str, logs),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                if captured.returncode in {0, 1}:
                    latency_path.write_text(captured.stdout, encoding="utf-8")
        completed = {}
        if latency_path.exists():
            for line in sorted(latency_path.read_text(encoding="utf-8").splitlines()):
                if "event=llm_stream_done " in line:
                    fields = dict(re.findall(r"\b([a-z_]+)=([^ ]+)", line))
                    if "call_index" in fields and "duration_ms" in fields:
                        completed[fields["call_index"]] = fields
        model_seconds = [float(r["duration_ms"]) / 1000 for r in completed.values()]
        summary["timing"] = {
            "model_done_samples": len(model_seconds),
            "model_done_complete": len(model_seconds) == snapshot.get("model_calls"),
            "model_seconds_sum": sum(model_seconds),
            "model_seconds_median": statistics.median(model_seconds) if model_seconds else None,
            "tool_seconds_sum": sum(r["seconds"] or 0 for r in results),
            "read_access_seconds_sum": sum(r["read_access_seconds"] or 0 for r in results),
            "prefix_relations": dict(
                collections.Counter(
                    r.get("wire_prefix_relation", "unknown") for r in completed.values()
                )
            ),
            "prompt_tokens_sum": sum(int(r.get("prompt_tokens", 0)) for r in completed.values()),
            "cached_prompt_tokens_sum": sum(
                int(r.get("cached_prompt_tokens", 0)) for r in completed.values()
            ),
        }
    if snapshot["status"] == "terminal" and (root / f"{args.prefix}-code.json").exists():
        expected = json.loads((root / f"{args.prefix}-code.json").read_text(encoding="utf-8"))
        summary["code_drift"] = [
            name
            for name, digest in expected.items()
            if not Path(name).is_file()
            or hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest
        ]
    write("summary", summary)
    if snapshot["status"] == "terminal":
        write(
            "manifest",
            [
                {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(artifacts)
            ],
        )
    last = results[-3:]
    budget = root / "desktop-budget.json"
    remaining = (
        max(0, int(json.loads(budget.read_text(encoding="utf-8"))["deadline"] - time.time()))
        if budget.exists() and snapshot["status"] != "terminal"
        else None
    )
    print(
        json.dumps(
            {
                "status": snapshot["status"],
                "outcome": snapshot.get("outcome"),
                "model_calls": snapshot.get("model_calls")
                if snapshot["status"] == "terminal"
                else None,
                "code_drift": summary.get("code_drift"),
                "timing": summary.get("timing"),
                "tool_count": len(results),
                "budget_remaining_seconds": remaining,
                "errors": len(summary["errors"]),
                "commands": summary["commands"],
                "last": [
                    {
                        "cmd": r["cmd"],
                        "error": r["error"],
                        "game": {
                            k: v
                            for k, v in r["game"].items()
                            if k
                            in {
                                "status",
                                "code",
                                "message",
                                "image_paths",
                                "budget_remaining_seconds",
                                "workflow",
                                "input_applied",
                                "revision",
                            }
                        },
                    }
                    for r in last
                ],
                "assistant_last": list(texts.values())[-1:]
                if snapshot["status"] == "terminal"
                else [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
