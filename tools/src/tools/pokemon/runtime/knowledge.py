"""Player-authored knowledge, grounded in screenshots, never emulator memory."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .protocol import GameError, atomic_json, controller_lock

SECTIONS = {"party", "bag", "goals", "places", "notes"}
TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
IMAGE_ID = re.compile(r"^[a-f0-9]{32}$")


def require_settled_controller(root: Path) -> None:
    state = root / "state.json"
    if state.exists() and json.loads(state.read_text(encoding="utf-8")).get("pending"):
        raise GameError("action_unknown", "游戏操作尚未对账，先 observe；不能写入或配对新记录。")


def screenshot(root: Path, image_id: str) -> Path:
    if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
        raise GameError("invalid_evidence", "来源必须是实际截图的 32 位编号。")
    path = (root / "screens" / f"{image_id}.png").resolve(strict=True)
    if not path.is_relative_to((root / "screens").resolve()) or path.stat().st_size > 1048576:
        raise GameError("invalid_evidence", "来源必须是本实例的原始截图。")
    return path


class Knowledge:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "knowledge.json"

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            data = {
                "schema": 1,
                "revision": 0,
                "epoch": 0,
                "timeline": "initial",
                "sections": {},
                "workflows": {},
            }
        # Read-only migration: preserve the old prose and provenance, but never
        # infer whether a player-authored fact is still true from button input.
        if data.get("schema", 1) < 2:
            legacy = self.root / "notes.json"
            prose = json.loads(legacy.read_text(encoding="utf-8")) if legacy.exists() else {}
            data.setdefault("journal", prose.get("text", ""))
        data["schema"] = 2
        data.setdefault("journal", "")
        for records in data["sections"].values():
            for fields in records.values():
                for field in fields.values():
                    field.pop("stale", None)
        return data

    def query(
        self,
        section: str | None = None,
        entity: str | None = None,
        *,
        details: bool = False,
        all_records: bool = False,
    ) -> dict[str, Any]:
        if (entity and not section) or (all_records and section):
            raise GameError("invalid_query", "entity 需要 section；all 与 section 不同时使用。")
        data = self.load()
        selected = data["sections"]
        if section:
            if section not in SECTIONS:
                raise GameError("invalid_section", "section 使用 party/bag/goals/places/notes。")
            selected = {section: selected.get(section, {})}
        elif not all_records:
            selected = {k: v for k, v in selected.items() if k in {"party", "bag"}}
        out: dict[str, Any] = {}
        for name, records in selected.items():
            out[name] = {}
            for key, fields in records.items():
                if entity and key != entity:
                    continue
                out[name][key] = {
                    "values": {k: v["value"] for k, v in fields.items()},
                }
                if details:
                    out[name][key]["metadata"] = {
                        k: {
                            name: value
                            for name, value in v.items()
                            if name not in {"value", "epoch"}
                        }
                        for k, v in fields.items()
                    }
        result = {
            "revision": data["revision"],
            "sections": out,
        }
        if section is None:
            result.update(
                {
                    "journal": data["journal"],
                    "index": {
                        name: list(records)
                        for name, records in data["sections"].items()
                        if name not in {"party", "bag"}
                    },
                    "workflows": [
                        {"id": k, "kind": v["kind"], "intent": v["intent"]}
                        for k, v in data["workflows"].items()
                    ],
                }
            )
        if data.get("restore_context"):
            result["restore_context"] = data["restore_context"]
        return result

    def update(
        self,
        changes: object,
        source: str | list[str] | None = None,
        *,
        certainty: str = "observed",
        revision: int | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        sources = [source] if isinstance(source, str) else list(source or [])
        for item in sources:
            screenshot(self.root, item)
        if certainty not in {"observed", "inferred"}:
            raise GameError("invalid_certainty", "certainty 使用 observed 或 inferred。")
        if (
            not isinstance(changes, dict)
            or (not changes and text is None)
            or set(changes) - SECTIONS
        ):
            raise GameError(
                "invalid_knowledge", "data 是以 party/bag/goals/places/notes 为键的对象。"
            )
        if text is not None and (not isinstance(text, str) or len(text) > 8000):
            raise GameError("notes_limit", "当前页必须是文本，最多 8000 字。")
        if len(json.dumps(changes, ensure_ascii=False, allow_nan=False)) + len(text or "") > 16000:
            raise GameError("knowledge_limit", "单次记录最多 16000 字，按变化局部更新。")
        evidence = []
        for item in sources:
            operation = self.root / "operations" / f"{item}.json"
            evidence.append(
                json.loads(operation.read_text(encoding="utf-8")) if operation.exists() else {}
            )
        with controller_lock(self.root):
            require_settled_controller(self.root)
            data = self.load()
            if any(
                item.get("knowledge_timeline", "initial") != data.get("timeline", "initial")
                for item in evidence
            ):
                raise GameError("old_timeline", "截图来自恢复前的时间线；重新观察后记录。")
            if revision is not None and revision != data["revision"]:
                raise GameError("stale_revision", "记录版本已变化；先查询，不能覆盖新事实。")
            candidate = copy.deepcopy(data)
            if text is not None:
                candidate["journal"] = text
            for section, records in changes.items():
                if not isinstance(records, dict) or len(records) > 100:
                    raise GameError("invalid_knowledge", "每个 section 是最多 100 个实体的对象。")
                for entity, fields in records.items():
                    if not TOKEN.fullmatch(entity) or (
                        fields is not None and (not isinstance(fields, dict) or not fields)
                    ):
                        raise GameError(
                            "invalid_knowledge", "实体使用稳定英文 ID，值是非空字段对象。"
                        )
                    if fields is None:
                        candidate["sections"].get(section, {}).pop(entity, None)
                        continue
                    target = candidate["sections"].setdefault(section, {}).setdefault(entity, {})
                    for field, value in fields.items():
                        if not TOKEN.fullmatch(field):
                            raise GameError("invalid_knowledge", "字段名使用英文、数字和下划线。")
                        target[field] = {
                            "value": value,
                            "sources": sources,
                            "certainty": certainty,
                            "updated_at": datetime.now(UTC).isoformat(),
                            "timeline": data.get("timeline", "initial"),
                        }
            candidate["revision"] += 1
            if len(json.dumps(candidate)) > 256000:
                raise GameError("knowledge_limit", "记录已满；精简现有实体，不重复写完整游玩流水。")
            self._save(data, candidate)
        return {"status": "recorded", "revision": candidate["revision"]}

    def _save(self, previous: dict[str, Any], candidate: dict[str, Any]) -> None:
        atomic_json(self.root / "knowledge-history" / f"{previous['revision']}.json", previous)
        atomic_json(self.path, candidate)

    def journal(self, text: str | None = None) -> dict[str, str]:
        if text is None:
            return {"text": self.load()["journal"]}
        self.update({}, text=text)
        return {"text": text}

    def checkpoint(self, operation_id: str) -> None:
        atomic_json(
            self.root / "knowledge-checkpoints" / f"{operation_id}.json",
            {
                "knowledge": self.load(),
                "notes": notes(self.root),
            },
        )

    def restore(self, operation_id: str, *, preserve_experience: bool = False) -> None:
        path = self.root / "knowledge-checkpoints" / f"{operation_id}.json"
        old = self.load()
        if path.exists():
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            restored = snapshot["knowledge"]
            prose = snapshot["notes"]
        else:
            restored = {"schema": 1, "epoch": 0, "sections": {}, "workflows": {}}
            prose = {"text": ""}
        if preserve_experience:
            restored["experience_timelines"] = list(
                dict.fromkeys(
                    [
                        *old.get("experience_timelines", []),
                        old.get("timeline", "initial"),
                    ]
                )
            )[-128:]
            for section in ("goals", "places", "notes"):
                remembered = copy.deepcopy(old["sections"].get(section, {}))
                restored["sections"][section] = remembered
            restored["workflows"] = copy.deepcopy(old["workflows"])
            prose = {
                **notes(self.root),
                "context": "保留上次尝试的经验；不能当作读档后的当前游戏状态。",
            }
        # Never restore a revision number that an old caller could reuse.
        restored["revision"] = old["revision"] + 1
        restored["timeline"] = uuid4().hex
        restored["schema"] = 2
        restored["journal"] = prose.get("text", "")
        restored["restore_context"] = {
            "checkpoint": operation_id,
            "records_available": path.exists(),
            "experience_preserved": preserve_experience,
            "note": "队伍/背包恢复为存档配套记录；缺配套记录则未知。保留的探索经验不代表当前事件已经完成。",
        }
        for records in restored["sections"].values():
            for fields in records.values():
                for field in fields.values():
                    field.pop("stale", None)
        self._save(old, restored)


def notes(root: Path, text: str | None = None) -> dict[str, str]:
    """Compatibility entry for the notebook's prose page, never a second store."""
    return Knowledge(root).journal(text)
