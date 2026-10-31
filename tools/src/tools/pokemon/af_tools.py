"""Game tools for the optional source runner, with pictures in the same result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from agent.attachments import AttachmentStore
from agent.image_reading import ImageReadingService
from agent.tools.protocol import ToolResult

from .runtime.client import GameClient
from .runtime.protocol import KEYS, GameError, atomic_json

OBSERVATION = {
    "type": "string",
    "pattern": "^[0-9a-f]{32}$",
    "description": "原样使用最近 pokemon_observe 或 pokemon_act 结果的 observation_id；旧画面编号不可用。",
}


class PokemonObserve:
    name: ClassVar[str] = "pokemon_observe"
    execution_kind = "serial"
    timeout_seconds = 25.0
    description: ClassVar[str] = (
        "观察已由宿主绑定的游戏，返回当前截图和 observation_id。初次操作、恢复或旧画面失效时使用。不接受任意文件路径。action_unknown 时只允许对账，不能重发旧动作。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, client: GameClient, assets: AttachmentStore) -> None:
        self.client = client
        self.images = ImageReadingService(assets)

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return self._execute("observe")

    def _execute(self, kind: str, **kwargs: Any) -> ToolResult:
        try:
            result = self.client.request(kind, **kwargs)
            if result["status"] != "completed":
                return ToolResult(text=json.dumps(result, ensure_ascii=False), is_error=True)
            screenshot = Path(result.pop("screenshot"))
            image = self.images.snapshot(
                screenshot.read_bytes(), display_name=f"pokemon-{result['observation_id']}.png"
            )
            return ToolResult(text=json.dumps(result, ensure_ascii=False), images=(image.image,))
        except (GameError, OSError, ValueError) as exc:
            return ToolResult(
                text=json.dumps(
                    {"code": getattr(exc, "code", "image_or_io_error"), "message": str(exc)},
                    ensure_ascii=False,
                ),
                is_error=True,
            )


class PokemonAct(PokemonObserve):
    name: ClassVar[str] = "pokemon_act"
    description: ClassVar[str] = (
        "在最近观察上执行有界按键并直接返回后续截图。一次最多 32 段、合计 600 帧；遇到菜单和岔路用短段。空 keys 表示松开等待；长按不等于反复点击。超时或 action_unknown 时先 pokemon_observe 对账，不能原样重试。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "observation_id": OBSERVATION,
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "description": "按执行顺序排列的按键段，总计不超过 600 帧。",
                "items": {
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "array",
                            "uniqueItems": True,
                            "maxItems": 10,
                            "description": "同时按住的键，空列表为松开；不能同时按相反方向。",
                            "items": {"type": "string", "enum": list(KEYS)},
                        },
                        "frames": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 600,
                            "description": "模拟器帧数，约 60 帧为一游戏秒；不是模型等待时长。",
                        },
                    },
                    "required": ["keys", "frames"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["observation_id", "steps"],
        "additionalProperties": False,
    }

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        return self._execute("act", observation_id=args["observation_id"], steps=args["steps"])


class PokemonNotes:
    name: ClassVar[str] = "pokemon_notes"
    execution_kind = "serial"
    description: ClassVar[str] = (
        "读取或替换当前游玩的简短笔记。记录自己从画面探索到的目标、路线和猜测，不注入攻略。省略 text 只读；提供 text 会整体替换旧笔记，最多 8000 字。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "maxLength": 8000,
                "description": "新的完整笔记；不传则只读取，空串表示清空。",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, root: Path) -> None:
        self.path = root / "notes.json"

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        if "text" in args:
            atomic_json(self.path, {"text": args["text"]})
        data = self.path.read_text(encoding="utf-8") if self.path.exists() else '{"text":""}'
        return ToolResult(text=data)


class PokemonFinish:
    name: ClassVar[str] = "pokemon_finish"
    execution_kind = "serial"
    description: ClassVar[str] = (
        "当实际看到名人堂、无法继续或需要用户介入时停止自动续玩。completion_pending 只是请求核验通关，不代表宿主已确认。必须说明可见证据或阻碍；不会修改游戏。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["completion_pending", "needs_attention"],
                "description": "通关画面待核验，或当前确实需要用户处理。",
            },
            "detail": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "实际看到的证据或无法自主处理的原因。",
            },
        },
        "required": ["reason", "detail"],
        "additionalProperties": False,
    }

    def __init__(self, root: Path) -> None:
        self.root = root

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        atomic_json(self.root / "finish.json", args)
        GameClient(self.root).pause()
        return ToolResult(
            text=json.dumps(
                {
                    "status": args["reason"],
                    "message": "已请求停止，保留当前游戏和证据，尚未判定通关。",
                },
                ensure_ascii=False,
            )
        )
