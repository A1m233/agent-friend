"""Optional local OCR mailbox; reads screenshots only and never writes facts."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from .knowledge import screenshot
from .protocol import atomic_json


def visual_aid(path: Path, source: str, result: dict[str, Any]) -> dict[str, Any]:
    """Enlarge pixels for the player; this is not a new game observation or OCR truth."""
    target = path.with_name(f"view-{source}-3x.png")
    temporary = target.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with Image.open(path) as image:
            image.resize((720, 480), Image.Resampling.NEAREST).save(temporary, format="PNG")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        **result,
        "source": source,
        "image_paths": [path.name, target.name],
        "visual_aid": "第二张是同一截图的3倍像素放大，不是新观察；游戏操作/记录仍使用原source。OCR高置信度也可能读错数字或斜线，数值与血条矛盾时先核对图，不直接用于用药判断。",
    }


def read_text(root: Path, source: str) -> dict[str, Any]:
    path = screenshot(root, source)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    folder = root / "ocr"
    output = folder / "results" / f"{digest}.json"
    if output.exists():
        cached: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
        return visual_aid(path, source, cached)
    heartbeat = folder / "heartbeat.json"
    if not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > 15:
        return visual_aid(
            path,
            source,
            {
                "status": "unavailable",
                "message": "本地 OCR 服务未运行；直接读原图，不影响手动记录与游玩。",
            },
        )
    atomic_json(folder / "requests" / f"{digest}.json", {"source": source, "sha256": digest})
    until = time.monotonic() + 8
    while time.monotonic() < until:
        if output.exists():
            result: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
            return visual_aid(path, source, result)
        time.sleep(0.05)
    return visual_aid(
        path,
        source,
        {
            "status": "pending",
            "message": "OCR 未在 8 秒内返回；可直接看图，稍后同源查询不会操作游戏。",
        },
    )
