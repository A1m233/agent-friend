"""Player save/load. Validate opaque files, never parse game state from them."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .client import ID, GameClient
from .protocol import GameError


def saved_record(root: Path, checkpoint_id: str, *, verify_hash: bool = False) -> dict[str, Any]:
    if not ID.fullmatch(checkpoint_id):
        raise GameError(
            "invalid_checkpoint", "使用 checkpoint/finish 或 saves 返回的 checkpoint_id。"
        )
    path = (root / "saves" / f"{checkpoint_id}.ss0").resolve()
    journal = root / "operations" / f"{checkpoint_id}.json"
    if not path.is_relative_to(root.resolve()) or not path.is_file() or not journal.is_file():
        raise GameError(
            "invalid_checkpoint", "本实例没有该存档；先 saves 查询，截图编号不能作为存档。"
        )
    record: dict[str, Any] = json.loads(journal.read_text(encoding="utf-8"))
    if (
        record.get("kind") != "checkpoint"
        or record.get("status") != "completed"
        or record.get("operation_id") != checkpoint_id
        or not record.get("save_sha256")
    ):
        raise GameError("invalid_checkpoint", "该编号不是已完成的 checkpoint。")
    if verify_hash and hashlib.sha256(path.read_bytes()).hexdigest() != record["save_sha256"]:
        raise GameError("checkpoint_changed", "存档文件校验不符；保持当前游戏，不加载该文件。")
    return record


def list_saves(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in (root / "saves").glob("*.ss0"):
        try:
            record = saved_record(root, path.stem)
        except (GameError, OSError, ValueError):
            continue
        records.append(
            {
                "checkpoint_id": path.stem,
                "saved_at_unix_seconds": record["started_at"],
                "screenshot": f"{path.stem}.png",
                "has_player_records": (
                    root / "knowledge-checkpoints" / f"{path.stem}.json"
                ).exists(),
            }
        )
    records.sort(key=lambda row: row["saved_at_unix_seconds"], reverse=True)
    return {"saves": records[:20], "total": len(records), "truncated": len(records) > 20}


def load_save(client: GameClient, checkpoint_id: str) -> dict[str, Any]:
    saved_record(client.root, checkpoint_id, verify_hash=True)
    budget_path = client.root / "desktop-budget.json"
    budget = json.loads(budget_path.read_text(encoding="utf-8")) if budget_path.exists() else None
    client.pause()
    try:
        restored = client.request("restore", restore_id=checkpoint_id, preserve_experience=True)
        if restored["status"] != "completed":
            raise GameError("load_failed", "读档未完成；保持暂停，先 observe 对账，不自动重试。")
        current_budget = (
            json.loads(budget_path.read_text(encoding="utf-8")) if budget_path.exists() else {}
        )
        if budget and (
            current_budget.get("id") != budget.get("id")
            or time.time() >= current_budget.get("deadline", 0)
        ):
            return {
                **restored,
                "loaded_checkpoint_id": checkpoint_id,
                "paused": True,
                "message": "已读档，但本轮预算已结束；保持暂停，可 finish 保存。",
            }
        client.resume()
        current = client.request("observe")
        return {
            **current,
            "loaded_checkpoint_id": checkpoint_id,
            "load_operation_id": restored["operation_id"],
            "message": "已读档。队伍/背包回到存档时的记录，缺记录则未知；保留探索经验和流程，旧目标/事件状态标为待核对。会话和本轮预算没有回退。",
        }
    except Exception:
        client.pause()
        raise
