"""Actual emulator assertions on the original test ROM, never on a user's save."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .runtime.client import GameClient
from .runtime.protocol import GameError, atomic_json, atomic_text
from .setup import RUNTIME_ROOT


def refresh_adapter(root: Path) -> dict[str, str]:
    client = GameClient(root)
    if client.config["language_claim"] != "homebrew-test" or not (root / "paused").exists():
        raise GameError("not_paused_test", "只允许更新已暂停的自制测试实例。")
    prelude = f'AF_ROOT = {json.dumps(root.as_posix(), ensure_ascii=False)}\nAF_ROM_CRC32 = "{client.config["rom_crc32"]}"\n'
    atomic_text(
        root / "bridge.lua",
        prelude + (RUNTIME_ROOT / "bridge.lua").read_text(encoding="utf-8"),
    )
    return {
        "status": "script_updated",
        "message": "mGBA 脚本窗口先重置，再重新载入 bridge.lua，才能验证新代码。",
    }


def verify(root: Path) -> dict[str, Any]:
    client = GameClient(root)
    if (
        client.config["language_claim"] != "homebrew-test"
        or client.config["rom_source"] != "Original AF smoke program; no Nintendo assets"
    ):
        raise GameError("not_test_instance", "此检查只能在专用自制测试 ROM 实例上运行。")

    def pixel(result: dict[str, Any]) -> tuple[int, ...]:
        with Image.open(result["screenshot"]) as im:
            assert im.size == (240, 160)
            value = im.convert("RGB").getpixel((0, 0))
            assert isinstance(value, tuple)
            return value

    records = []
    try:
        client.resume()
        observed = client.request("observe")
        records.append(observed)
        pressed = client.request(
            "act", observation_id=observed["observation_id"], steps=[{"keys": ["A"], "frames": 4}]
        )
        records.append(pressed)
        assert pixel(pressed) == (0, 255, 0), pixel(pressed)
        released = client.request(
            "act", observation_id=pressed["observation_id"], steps=[{"keys": [], "frames": 4}]
        )
        records.append(released)
        assert pixel(released) == (255, 0, 0), pixel(released)
        saved = client.request("checkpoint", observation_id=released["observation_id"])
        records.append(saved)
        assert Path(saved["save_path"]).stat().st_size > 0
        cancel = threading.Timer(0.15, client.pause)
        cancel.start()
        try:
            interrupted = client.request(
                "act",
                observation_id=saved["observation_id"],
                steps=[{"keys": ["A"], "frames": 600}],
            )
            records.append(interrupted)
        finally:
            cancel.cancel()
            cancel.join()
        assert interrupted["status"] == "cancelled", interrupted
        assert interrupted["frames"] < 600
        after = client.request("observe")
        records.append(after)
        assert pixel(after) == (255, 0, 0), pixel(after)
        restored = client.request("restore", restore_id=saved["operation_id"])
        records.append(restored)
        assert restored["status"] == "completed"
        assert pixel(restored) == (255, 0, 0), pixel(restored)
        result = {
            "status": "passed",
            "model_calls": 0,
            "checked_at": time.time(),
            "adapter_sha256": hashlib.sha256((root / "bridge.lua").read_bytes()).hexdigest(),
            "rom_sha256": client.config["rom_sha256"],
            "records": records,
            "limits": "Original smoke ROM only; no FireRed Chinese, battery-save roundtrip, or AF completion evidence.",
        }
        atomic_json(root / "emulator-check.json", result)
        return result
    finally:
        client.pause()
