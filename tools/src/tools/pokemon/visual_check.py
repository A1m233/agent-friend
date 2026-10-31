"""Explicit no-LLM smoke; always restore the paused game's pre-test checkpoint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runtime.actions import VisualActions
from .runtime.client import GameClient
from .runtime.protocol import GameError, atomic_json, atomic_text, controller_lock


def verify(root: Path, *, movement: bool = False) -> dict[str, Any]:
    client = GameClient(root)
    if not client.status()["paused"] or client.status().get("pending"):
        raise GameError("smoke_requires_paused", "先暂停并完成对账；不会接管正在游玩的实例。")
    supervisor = root / "supervisor"
    supervisor.mkdir(exist_ok=True)
    report: dict[str, Any] = {"kind": "no_llm_visual_smoke", "started_at": time.time()}
    output = root / f"visual-smoke-{uuid4().hex}.json"
    with controller_lock(supervisor):
        client.supervisor_owned = True
        before = client.request("observe")
        checkpoint = client.request("checkpoint", observation_id=before["observation_id"])
        report["before"] = before
        report["checkpoint"] = checkpoint
        history_path = root / "visual-history.json"
        history_before = history_path.read_text(encoding="utf-8") if history_path.exists() else None
        try:
            client.resume()
            initial = client.request("observe")
            report["dialogue"] = VisualActions(client).dialogue(initial["observation_id"])
            if movement:
                report["movement"] = VisualActions(client).movement(
                    report["dialogue"]["observation_id"],
                    [{"keys": ["RIGHT"], "frames": 8}],
                    "无模型验证前后图，随后恢复原存档",
                )
                report["wait"] = VisualActions(client).movement(
                    report["movement"]["observation_id"],
                    [{"keys": [], "frames": 12}],
                    "无模型验证等待就绪",
                    wait_only=True,
                )
        finally:
            client.pause()
            try:
                report["restore"] = client.request("restore", restore_id=checkpoint["operation_id"])
            finally:
                client.pause()
                if history_before is None:
                    history_path.unlink(missing_ok=True)
                else:
                    atomic_text(history_path, history_before)
                report["exploration_history_restored"] = True
                report["finished_at"] = time.time()
                report["paused"] = client.status()["paused"]
                atomic_json(output, report)
    return {
        "status": "completed",
        "report": str(output),
        "dialogue": report["dialogue"]["dialogue"],
        "restored": report["restore"]["status"],
        "paused": report["paused"],
        "movement_images": len(report.get("movement", {}).get("image_paths", [])),
        "wait_ready": report.get("wait", {}).get("visual", {}).get("settled"),
    }
