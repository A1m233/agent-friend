"""Explicit no-LLM smoke; always restore the paused game's pre-test checkpoint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runtime.actions import VisualActions
from .runtime.client import GameClient
from .runtime.knowledge import Knowledge
from .runtime.protocol import GameError, atomic_json, atomic_text, controller_lock
from .runtime.saves import load_save
from .runtime.workflows import replay


def verify(
    root: Path,
    *,
    movement: bool = False,
    inspection_workflow: str | None = None,
    player_load: bool = False,
) -> dict[str, Any]:
    if sum((movement, bool(inspection_workflow), player_load)) > 1:
        raise GameError("conflicting_smoke", "movement、inspection、player-load 分开验证。")
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
        knowledge_before = Knowledge(root).load()
        try:
            client.resume()
            initial = client.request("observe")
            Knowledge(root).update(
                {"notes": {"smoke": {"purpose": "temporary no-LLM verification"}}},
                initial["observation_id"],
            )
            if player_load:
                initial = load_save(client, checkpoint["operation_id"])
                report["player_load"] = initial
                report["player_load_experience_retained"] = "smoke" in Knowledge(root).load()[
                    "sections"
                ].get("notes", {})
            report["dialogue"] = (
                {**initial, "dialogue": {"stop_reason": "player_load_only", "advance_presses": 0}}
                if player_load
                else VisualActions(client).dialogue(initial["observation_id"])
            )
            if inspection_workflow:
                menu = VisualActions(client).movement(
                    report["dialogue"]["observation_id"],
                    [{"keys": ["START"], "frames": 8}, {"keys": [], "frames": 24}],
                    "从已确认世界画面打开主菜单，检查已录制的队伍查看流程",
                )
                report["inspection"] = replay(
                    client, inspection_workflow, menu["observation_id"], "inspect"
                )
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
                restored_knowledge = Knowledge(root).load()
                report["player_knowledge_restored"] = all(
                    restored_knowledge[key] == knowledge_before[key]
                    for key in ("sections", "workflows", "epoch")
                )
                report["finished_at"] = time.time()
                report["paused"] = client.status()["paused"]
                atomic_json(output, report)
    return {
        "status": "completed",
        "report": str(output),
        "dialogue": report["dialogue"]["dialogue"],
        "restored": report["restore"]["status"],
        "paused": report["paused"],
        "player_knowledge_restored": report["player_knowledge_restored"],
        "movement_images": len(report.get("movement", {}).get("image_paths", [])),
        "wait_ready": report.get("wait", {}).get("visual", {}).get("settled"),
        "inspection": report.get("inspection", {}).get("workflow"),
        "player_load_experience_retained": report.get("player_load_experience_retained"),
    }
