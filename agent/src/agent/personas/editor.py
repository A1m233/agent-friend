"""人格编辑 Agent 的专用 prompt builder 与候选提案工具。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar

from ..errors import PersonaPersistError
from ..system_prompt.sections import PromptChannel
from ..tools import ToolResult
from .effective import (
    EffectivePersonaStore,
    extract_persona_display_name,
    normalize_persona_content,
)


class PersonaEditorWorkspace:
    """持久化编辑对话最初绑定的正式人格 revision。"""

    def __init__(self, store: EffectivePersonaStore, state_file: Path) -> None:
        self._store = store
        self._state_file = state_file
        self._lock = threading.RLock()

    def exists(self) -> bool:
        return self._state_file.is_file()

    def start(self) -> str:
        revision = self._store.load().revision
        with self._lock:
            self._atomic_write(revision + "\n")
        return revision

    def base_revision(self) -> str:
        with self._lock:
            try:
                revision = self._state_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise PersonaPersistError("人格编辑基准不存在，请放弃当前编辑后重试") from exc
            if not revision.startswith("sha256:"):
                raise PersonaPersistError("人格编辑基准已损坏，请放弃当前编辑后重试")
            return revision

    def clear(self) -> None:
        with self._lock:
            try:
                self._state_file.unlink(missing_ok=True)
            except OSError as exc:
                raise PersonaPersistError(f"清理人格编辑基准失败: {exc}") from exc

    def _atomic_write(self, text: str) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self._state_file.name}.",
                suffix=".tmp",
                dir=str(self._state_file.parent),
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self._state_file)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise PersonaPersistError(f"保存人格编辑基准失败: {exc}") from exc


class PersonaEditorPromptBuilder:
    """每个编辑回合读取最新正式人格，编辑历史由专用 Session 持有。"""

    def __init__(self, store: EffectivePersonaStore) -> None:
        self._store = store
        self._template = (
            files("agent.prompt_sections")
            .joinpath("persona_editor.md")
            .read_text(encoding="utf-8")
            .strip()
        )

    def build(
        self,
        *,
        channel_override: PromptChannel | None = None,
    ) -> str:
        del channel_override
        return self._template.format(current_persona=self._store.load().content)

    def snapshot(self) -> _FrozenPersonaEditorPromptBuilder:
        return _FrozenPersonaEditorPromptBuilder(self.build())


class _FrozenPersonaEditorPromptBuilder:
    """冻结单个用户回合的正式人格快照，工具循环中不重新读取文件。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def build(
        self,
        *,
        channel_override: PromptChannel | None = None,
    ) -> str:
        del channel_override
        return self._text

    def snapshot(self) -> _FrozenPersonaEditorPromptBuilder:
        return self


class ProposePersonaTool:
    """把模型提出的完整候选稿收敛成可被客户端识别的结构化结果。"""

    name: ClassVar[str] = "propose_persona"
    description: ClassVar[str] = (
        "当已经理解用户想要的人格变化时调用。提交完整 persona.md 候选稿和 1 到 5 条变化摘要；"
        "不要提交局部 patch，也不要在仍需向用户澄清时调用。"
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "完整、可直接保存的 persona.md 正文",
            },
            "summary": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
                "description": "相对上一版本的简短变化摘要",
            },
        },
        "required": ["content", "summary"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        store: EffectivePersonaStore,
        base_revision_provider: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._base_revision_provider = base_revision_provider or (
            lambda: self._store.load().revision
        )

    def invoke(self, args: dict[str, Any]) -> ToolResult:
        content = args.get("content")
        summary = args.get("summary")
        if not isinstance(content, str) or not isinstance(summary, list):
            return ToolResult("候选稿必须包含完整 content 和 summary", is_error=True)
        try:
            normalized = normalize_persona_content(content).rstrip()
        except Exception as exc:
            return ToolResult(f"候选人格不可用：{exc}", is_error=True)
        items = [item.strip() for item in summary if isinstance(item, str) and item.strip()]
        if not 1 <= len(items) <= 5:
            return ToolResult("summary 必须包含 1 到 5 条非空变化", is_error=True)
        payload = {
            "kind": "persona_candidate",
            "content": normalized,
            "displayName": extract_persona_display_name(normalized),
            "summary": items,
            "baseRevision": self._base_revision_provider(),
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False))


__all__ = ["PersonaEditorPromptBuilder", "PersonaEditorWorkspace", "ProposePersonaTool"]
