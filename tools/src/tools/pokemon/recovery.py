"""Explicit host-failure rollback; never exposed as a model gameplay tool."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.paths import sessions_dir
from agent.sessions.events import ROLLOUT_EVENT_TYPES

from agent import JsonlSessionStore, Session

from .runtime.client import ID, GameClient
from .runtime.protocol import GameError, atomic_json, controller_lock


def recover(
    root: Path, checkpoint_id: str, reason: str, *, session_root: Path | None = None
) -> dict[str, Any]:
    if not ID.fullmatch(checkpoint_id) or not reason.strip():
        raise GameError("invalid_recovery", "给出 checkpoints 目录中的 ID 以及实际故障原因。")
    client = GameClient(root)
    supervisor = root / "supervisor"
    supervisor.mkdir(exist_ok=True)
    with controller_lock(supervisor):
        client.supervisor_owned = True
        client.pause()
        if (root / "recovery_pending.json").exists():
            raise GameError(
                "recovery_unknown", "已有未提交恢复；保留暂停状态，需要先核对它的执行证据。"
            )
        manifest = json.loads(
            (root / "checkpoints" / checkpoint_id / "manifest.json").read_text(encoding="utf-8")
        )
        play = json.loads((root / "play.json").read_text(encoding="utf-8"))
        if (
            manifest["rom_sha256"] != client.config["rom_sha256"]
            or manifest["skill_digest"] != play["skill_digest"]
        ):
            raise GameError("checkpoint_identity", "恢复点游戏或 Skill 身份与任务不同。")
        files = []
        for item in manifest["files"]:
            path = Path(item["path"]).resolve(strict=True)
            if (
                not path.is_relative_to(root.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise GameError("checkpoint_corrupt", "恢复点文件不在实例目录内或哈希不匹配。")
            files.append(path)
        session_path = root / "checkpoints" / checkpoint_id / "session.json"
        notes_path = session_path.with_name("notes.json")
        save_path = root / "saves" / f"{manifest['operation_id']}.ss0"
        if (
            session_path.resolve() not in files
            or notes_path.resolve() not in files
            or save_path.resolve() not in files
        ):
            raise GameError("checkpoint_incomplete", "恢复点缺少游戏、会话或笔记。")
        snapshot = json.loads(session_path.read_text(encoding="utf-8"))
        # Preserve settled business evidence; old rollout cache objects belong to the old session.
        snapshot["events"] = [e for e in snapshot["events"] if e["type"] not in ROLLOUT_EVENT_TYPES]
        new_id = str(uuid4())
        snapshot["events"][0]["uuid"] = new_id
        snapshot["events"][0]["payload"].update(
            origin_kind="pokemon_recovery", origin_id=checkpoint_id
        )
        branch = Session.from_dict(snapshot)
        journal = {
            "schema": 1,
            "status": "pending",
            "checkpoint_id": checkpoint_id,
            "reason": reason,
            "previous_session_id": play["session_id"],
            "new_session_id": new_id,
            "started_at": time.time(),
            "rollback": True,
        }
        atomic_json(root / "recovery_pending.json", journal)
        # A crash at any later boundary remains blocked, never silently replays restore.
        client.request("observe")
        result = client.request("restore", restore_id=manifest["operation_id"])
        if result["status"] != "completed":
            raise GameError("restore_not_completed", "模拟器恢复未成功；保留恢复日志和暂停状态。")
        JsonlSessionStore(session_root or sessions_dir()).create(branch)
        atomic_json(root / "notes.json", json.loads(notes_path.read_text(encoding="utf-8")))
        play.update(
            session_id=new_id,
            emulator_boot=result["emulator_boot"],
            status="paused",
            restored_checkpoint=checkpoint_id,
        )
        atomic_json(root / "play.json", play)  # retain the original deadline and spent call budget
        journal.update(
            status="completed", operation_id=result["operation_id"], finished_at=time.time()
        )
        atomic_json(root / "recoveries" / f"{new_id}.json", journal)
        (root / "recovery_pending.json").unlink()
        return journal
