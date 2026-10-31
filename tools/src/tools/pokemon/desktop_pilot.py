"""Observe one user-started desktop run and stop it at a bounded deadline.

This harness never submits messages, makes model requests or grants permissions.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.paths import log_dir

from .log_tail import RotatingLogTail
from .runtime.client import GameClient
from .runtime.protocol import GameError, atomic_json


def watch(root: Path, *, session_id: str, max_seconds: int, output: Path) -> dict[str, Any]:
    if not 1 <= max_seconds <= 3600:
        raise GameError("invalid_budget", "桌面试玩观察器仅接受 1–3600 秒的单轮时限。")
    if output.exists():
        raise GameError("evidence_exists", "拒绝覆盖已有试玩证据。")
    client = GameClient(root)
    budget_path = root / "desktop-budget.json"
    if budget_path.exists():
        raise GameError("pilot_exists", "已有桌面试玩预算；先核实原观察器已退出。")
    base = "http://127.0.0.1:18800/v1"

    def api(path: str, *, post: bool = False) -> dict[str, Any]:
        request = urllib.request.Request(base + path, data=b"" if post else None)
        with urllib.request.urlopen(request, timeout=2) as response:
            payload: dict[str, Any] = json.load(response)
            return payload

    baseline = {r["run_id"] for r in api("/runs")["runs"]}
    identity = uuid4().hex
    started = time.monotonic()
    deadline = time.time() + max_seconds
    result: dict[str, Any] = {
        "id": identity,
        "session_id": session_id,
        "armed_at": time.time(),
        "max_seconds": max_seconds,
        "deadline": deadline,
        "calls": [],
        "initial_operations": sorted(p.name for p in (root / "operations").glob("*.json")),
    }
    run = None
    tail = RotatingLogTail(log_dir() / "agent_bridge.log")
    atomic_json(budget_path, {"id": identity, "deadline": deadline})
    atomic_json(output, result)
    print(
        json.dumps({"status": "armed", "session_id": session_id, "max_seconds": max_seconds}),
        flush=True,
    )

    def cancel() -> None:
        if run is not None:
            query = urllib.parse.urlencode(
                {"expectedRuntimeInstanceId": run["runtime_instance_id"]}
            )
            result["cancel_result"] = api(
                f"/sessions/{session_id}/runs/{run['run_id']}/cancel?{query}", post=True
            )

    try:
        reported = 0.0
        while True:
            elapsed = time.monotonic() - started
            matches = [
                r
                for r in api("/runs")["runs"]
                if r["session_id"] == session_id
                and r["run_id"] not in baseline
                and r.get("control_scope") == "desktop_text"
            ]
            if run is None and matches:
                run = min(matches, key=lambda r: r["created_at"])
                result["run_id"] = run["run_id"]
            if run is not None:
                run = api("/runs/" + run["run_id"])
                result["snapshot"] = run
                for line in tail.readlines():
                    if (
                        f"run_id={run['run_id']} " in line
                        and "event=llm_transport " in line
                        and "phase=request_started " in line
                    ):
                        result["calls"].append(line.strip())
                if run["status"] == "terminal":
                    result["observed_model_calls"] = len(
                        {
                            match.group(1)
                            for line in result["calls"]
                            if (match := re.search(r"\bcall_index=(\d+)\b", line))
                        }
                    )
                    result["calls_match_final"] = result["observed_model_calls"] == run.get(
                        "model_calls"
                    )
                    result["stop_reason"] = "run_terminal"
                    break
            if elapsed >= max_seconds:
                result["stop_reason"] = "time_budget"
                cancel()
                break
            if elapsed - reported >= 15:
                atomic_json(output, result)
                print(
                    json.dumps(
                        {
                            "elapsed": round(elapsed),
                            "calls": len(result["calls"]),
                            "run_id": result.get("run_id"),
                        }
                    ),
                    flush=True,
                )
                reported = elapsed
            time.sleep(0.25)
    except BaseException as exc:
        result.update(stop_reason="watchdog_error", error=str(exc))
        try:
            cancel()
        except Exception as cancel_error:
            result["cancel_error"] = str(cancel_error)
        raise
    finally:
        result["log_observation_errors"] = tail.errors
        result["pause"] = client.pause()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["finished_at"] = time.time()
        atomic_json(output, result)
        if json.loads(budget_path.read_text(encoding="utf-8"))["id"] == identity:
            budget_path.unlink()
    return result
