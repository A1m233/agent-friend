"""Validate a small, data-only wire protocol before touching the emulator."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

KEYS = {
    "A": 1,
    "B": 2,
    "SELECT": 4,
    "START": 8,
    "RIGHT": 16,
    "LEFT": 32,
    "UP": 64,
    "DOWN": 128,
    "R": 256,
    "L": 512,
}
MAX_FRAMES = 600
MAX_STEPS = 32


class GameError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def steps_wire(steps: object) -> str:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise GameError("invalid_steps", "steps 必须包含 1–32 段按键。")
    total = 0
    encoded = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"keys", "frames"}:
            raise GameError("invalid_step", "每段只能包含 keys 和 frames。")
        frames, keys = step["frames"], step["keys"]
        if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
            raise GameError("invalid_frames", "frames 必须为 1–600 的整数。")
        if not isinstance(keys, list) or any(not isinstance(k, str) or k not in KEYS for k in keys):
            raise GameError(
                "invalid_keys",
                "keys 使用 A/B/SELECT/START/RIGHT/LEFT/UP/DOWN/R/L；空列表表示等待。",
            )
        if (
            len(keys) != len(set(keys))
            or {"LEFT", "RIGHT"} <= set(keys)
            or {"UP", "DOWN"} <= set(keys)
        ):
            raise GameError("conflicting_keys", "按键不能重复或包含相反方向。")
        total += frames
        encoded.append(f"{sum(KEYS[k] for k in keys)}:{frames}")
    if total > MAX_FRAMES:
        raise GameError("frame_limit", "一批总计不能超过 600 帧；缩短动作后重新观察。")
    return ",".join(encoded)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def controller_lock(root: Path) -> Iterator[None]:
    """OS-owned lock: process death releases it; never delete/recreate the lock inode."""
    with (root / "controller.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise GameError(
                "controller_busy", "另一个控制请求仍在运行；等待它结束，不要启动第二个控制者。"
            ) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
