"""Durable commands with conservative handling of missing emulator receipts."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .protocol import GameError, atomic_json, atomic_text, controller_lock, steps_wire

ID = re.compile(r"^[0-9a-f]{32}$")


class GameClient:
    def __init__(self, root: Path, *, timeout: float = 20.0) -> None:
        self.root = root.resolve(strict=True)
        self.config = json.loads((self.root / "instance.json").read_text(encoding="utf-8"))
        if self.config.get("schema") != 1:
            raise GameError("incompatible_instance", "实例协议不匹配，请使用创建该实例的客户端。")
        self.timeout = timeout
        self.supervisor_owned = False

    def _check_supervisor(self) -> None:
        supervisor = self.root / "supervisor"
        if not self.supervisor_owned and supervisor.exists():
            with controller_lock(supervisor):
                pass

    def _state(self) -> dict[str, Any]:
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    def status(self) -> dict[str, Any]:
        state = self._state()
        return {
            **state,
            "paused": (self.root / "paused").exists(),
            "instance_root": str(self.root),
            "screens_root": str(self.root / "screens"),
            "instance_id": self.config["instance_id"],
            "rom_sha256": self.config["rom_sha256"],
        }

    def pause(self) -> dict[str, Any]:
        # Must not wait for the command lock; the active command needs this signal.
        atomic_text(self.root / "paused", "user_pause\n")
        atomic_text(self.root / "cancel", "cancel\n")
        return {
            "status": "pause_requested",
            "message": "自动续玩已禁止；模拟器下一次帧回调将释放按键。",
        }

    def resume(self) -> dict[str, Any]:
        self._check_supervisor()
        with controller_lock(self.root):
            self._check_supervisor()
            if self.status().get("pending"):
                raise GameError("action_unknown", "先 observe 对账，不能直接恢复。")
            self.root.joinpath("paused").unlink(missing_ok=True)
            self.root.joinpath("cancel").unlink(missing_ok=True)
            state = self._state()
            state["observation_id"] = None
            atomic_json(self.root / "state.json", state)
        return {"status": "ready", "message": "已解除暂停；执行动作前重新 observe。"}

    def request(
        self,
        kind: str,
        *,
        observation_id: str | None = None,
        steps: object = None,
        cancel_check: Callable[[], bool] | None = None,
        restore_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"observe", "act", "checkpoint", "restore"}:
            raise GameError("invalid_operation", "仅支持 observe、act、checkpoint。")
        self._check_supervisor()
        encoded = steps_wire(steps) if kind == "act" else "-"
        if kind == "restore":
            if not restore_id or not ID.fullmatch(restore_id):
                raise GameError("invalid_restore", "恢复标识必须来自已校验的配对 checkpoint。")
            encoded = restore_id
        with controller_lock(self.root):
            self._check_supervisor()
            state = self._state()
            if state.get("pending"):
                if kind != "observe":
                    raise GameError(
                        "action_unknown", "上一动作结果不确定；先 observe 对账，禁止重放。"
                    )
                self._reconcile(state)
                state = self._state()
            if kind == "restore" and not (self.root / "paused").exists():
                raise GameError("pause_required", "恢复前必须暂停自动续玩。")
            if kind not in {"observe", "restore"}:
                if kind == "act" and (self.root / "paused").exists():
                    raise GameError("paused", "任务已暂停；用户明确恢复后才能操作。")
                if not observation_id or observation_id != state.get("observation_id"):
                    raise GameError(
                        "stale_observation",
                        "观察已过期或缺失；先 observe，再使用其 observation_id。",
                    )
            op = uuid4().hex
            start = time.time()
            record = {
                "operation_id": op,
                "kind": kind,
                "status": "pending",
                "started_at": start,
                "previous_observation_id": state.get("observation_id"),
                "steps": steps,
            }
            atomic_json(self.root / "operations" / f"{op}.json", record)
            state["pending"] = op
            atomic_json(self.root / "state.json", state)
            # Pending is durable before publication; a crash here is deliberately unknown.
            atomic_text(
                self.root / "request.txt", f"1|{op}|{kind}|{int(start + self.timeout)}|{encoded}\n"
            )
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if cancel_check and cancel_check():
                    self.pause()
                result = self._receipt(op)
                if result is not None:
                    return self._commit(state, record, result)
                time.sleep(0.025)
            record.update(status="unknown", elapsed_seconds=time.time() - start)
            atomic_json(self.root / "operations" / f"{op}.json", record)
            self.pause()
            raise GameError(
                "action_unknown",
                "模拟器未确认结果，已请求暂停。先恢复模拟器回调并 observe 对账，不要重放动作。",
            )

    def _receipt(self, op: str) -> dict[str, Any] | None:
        path = self.root / "receipts" / f"{op}.txt"
        if not path.exists():
            return None
        parts = path.read_text(encoding="utf-8").strip().split("|")
        if (
            len(parts) != 5
            or parts[0] != op
            or parts[1] not in {"completed", "cancelled", "failed"}
        ):
            raise GameError("invalid_receipt", "模拟器回执无效；保留未知状态并停止。")
        _, status, frames, code, boot = parts
        if (
            not frames.isdigit()
            or not re.fullmatch(r"[a-z0-9_]+", code)
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", boot)
        ):
            raise GameError("invalid_receipt", "模拟器回执字段无效；停止并检查适配器。")
        result: dict[str, Any] = {
            "operation_id": op,
            "status": status,
            "frames": int(frames),
            "code": code,
            "emulator_boot": boot,
        }
        image = self.root / "screens" / f"{op}.png"
        if status == "completed":
            if (
                not image.is_file()
                or image.stat().st_size > 8 * 1024 * 1024
                or not image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            ):
                raise GameError(
                    "invalid_screenshot", "已收到回执但没有有效 PNG；停止，不能将动作当作未执行。"
                )
            result.update(observation_id=op, screenshot=str(image))
        return result

    def _commit(
        self, state: dict[str, Any], record: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        op = record["operation_id"]
        record.update(result, elapsed_seconds=time.time() - record["started_at"])
        if result["status"] == "completed" and record["kind"] == "checkpoint":
            save = self.root / "saves" / f"{op}.ss0"
            if not save.is_file() or save.stat().st_size == 0:
                raise GameError("missing_save", "存档回执缺少有效文件；恢复点未提交。")
            record["save_sha256"] = hashlib.sha256(save.read_bytes()).hexdigest()
            record["save_path"] = str(save)
            record["checkpoint_scope"] = "emulator_only_not_af_context"
        atomic_json(self.root / "operations" / f"{op}.json", record)
        # Every terminal command invalidates the preceding image, even a partial cancellation.
        state.update(pending=None, observation_id=result.get("observation_id"), last_operation=op)
        atomic_json(self.root / "state.json", state)
        return record

    def _reconcile(self, state: dict[str, Any]) -> None:
        op = state["pending"]
        if not isinstance(op, str) or not ID.fullmatch(op):
            raise GameError("invalid_pending", "操作日志损坏，不能自动恢复。")
        result = self._receipt(op)
        if result is None:
            self.pause()
            raise GameError(
                "action_unknown", "旧动作仍无回执；保持暂停。重启或恢复模拟器后对账，不覆盖旧请求。"
            )
        record = json.loads((self.root / "operations" / f"{op}.json").read_text(encoding="utf-8"))
        self._commit(state, record, result)
