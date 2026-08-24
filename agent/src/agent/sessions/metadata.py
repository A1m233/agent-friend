"""会话管理 metadata 存储。

本模块保存“展示标题 / 归档状态”这类会话管理 UI 状态。它不参与
``Session`` 事件流，也不改变 JSONL 首行，避免把列表视图状态混进对话事实。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import portalocker
from portalocker.exceptions import LockException

from ..approvals.models import SessionApprovalMode, SessionApprovalModeState
from .errors import SessionPersistError

_LOCK_TIMEOUT_SECONDS = 10.0
_VERSION = 1
_T = TypeVar("_T")


@dataclass(frozen=True)
class SessionMetadata:
    """单个 session 的管理 metadata。"""

    display_title: str | None = None
    archived_at: datetime | None = None
    approval_mode: SessionApprovalMode = "per_request"
    approval_mode_version: int = 0


class SessionMetadataStore:
    """以单个 JSON 文件保存 session 管理 metadata。

    写操作在 ``metadata.json.lock`` 上取独占锁，并在锁内重新读改写，避免多个
    桌面窗口同时操作时互相覆盖。
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> dict[str, SessionMetadata]:
        return self._read()

    def get(self, session_id: str) -> SessionMetadata:
        return self._read().get(session_id, SessionMetadata())

    def set_display_title(self, session_id: str, title: str | None) -> SessionMetadata:
        def mutate(items: dict[str, SessionMetadata]) -> SessionMetadata:
            current = items.get(session_id, SessionMetadata())
            next_item = SessionMetadata(
                display_title=title,
                archived_at=current.archived_at,
                approval_mode=current.approval_mode,
                approval_mode_version=current.approval_mode_version,
            )
            _set_or_delete(items, session_id, next_item)
            return next_item

        return self._mutate(mutate)

    def set_archived_at(
        self,
        session_id: str,
        archived_at: datetime | None,
    ) -> SessionMetadata:
        def mutate(items: dict[str, SessionMetadata]) -> SessionMetadata:
            current = items.get(session_id, SessionMetadata())
            next_item = SessionMetadata(
                display_title=current.display_title,
                archived_at=archived_at,
                approval_mode=current.approval_mode,
                approval_mode_version=current.approval_mode_version,
            )
            _set_or_delete(items, session_id, next_item)
            return next_item

        return self._mutate(mutate)

    def compare_and_set_approval_mode(
        self,
        session_id: str,
        *,
        expected_version: int,
        mode: SessionApprovalMode,
    ) -> tuple[SessionApprovalModeState, bool]:
        """Atomically update one session's approval mode.

        Returns the effective state and whether this call changed it.  A stale
        expected version returns the current state with ``False`` and never
        writes, allowing callers to surface a deterministic conflict.
        """

        if expected_version < 0:
            raise ValueError("approval mode version must be non-negative")
        if mode not in {"per_request", "session_auto"}:
            raise ValueError(f"invalid approval mode: {mode}")

        def mutate(
            items: dict[str, SessionMetadata],
        ) -> tuple[SessionApprovalModeState, bool]:
            current = items.get(session_id, SessionMetadata())
            current_state = SessionApprovalModeState(
                current.approval_mode,
                current.approval_mode_version,
            )
            if current.approval_mode_version != expected_version:
                return current_state, False
            if current.approval_mode == mode:
                return current_state, False
            next_item = SessionMetadata(
                display_title=current.display_title,
                archived_at=current.archived_at,
                approval_mode=mode,
                approval_mode_version=current.approval_mode_version + 1,
            )
            _set_or_delete(items, session_id, next_item)
            return (
                SessionApprovalModeState(
                    next_item.approval_mode,
                    next_item.approval_mode_version,
                ),
                True,
            )

        return self._mutate(mutate)

    def delete(self, session_id: str) -> None:
        self.delete_many([session_id])

    def delete_many(self, session_ids: Iterable[str]) -> None:
        ids = set(session_ids)
        if not ids:
            return

        def mutate(items: dict[str, SessionMetadata]) -> None:
            for session_id in ids:
                items.pop(session_id, None)
            return None

        self._mutate(mutate)

    def _mutate(self, fn: Callable[[dict[str, SessionMetadata]], _T]) -> _T:
        self._ensure_parent()
        try:
            with portalocker.Lock(
                self._lock_path,
                "a",
                encoding="utf-8",
                timeout=_LOCK_TIMEOUT_SECONDS,
            ):
                items = self._read_unlocked()
                result = fn(items)
                self._write_unlocked(items)
                return result
        except LockException as e:
            raise SessionPersistError(
                f"获取会话 metadata 文件锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {self._path}: {e}"
            ) from e

    def _read(self) -> dict[str, SessionMetadata]:
        try:
            return self._read_unlocked()
        except OSError as e:
            raise SessionPersistError(f"读取会话 metadata 失败: {self._path}: {e}") from e

    def _read_unlocked(self) -> dict[str, SessionMetadata]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SessionPersistError(f"会话 metadata JSON 损坏: {self._path}: {e}") from e
        except OSError as e:
            raise SessionPersistError(f"读取会话 metadata 失败: {self._path}: {e}") from e
        if not isinstance(raw, dict):
            raise SessionPersistError(f"会话 metadata 顶层必须是 object: {self._path}")
        sessions = raw.get("sessions", {})
        if not isinstance(sessions, dict):
            raise SessionPersistError(f"会话 metadata.sessions 必须是 object: {self._path}")
        result: dict[str, SessionMetadata] = {}
        for session_id, value in sessions.items():
            if not isinstance(session_id, str) or not isinstance(value, dict):
                continue
            result[session_id] = _parse_metadata(value)
        return result

    def _write_unlocked(self, items: dict[str, SessionMetadata]) -> None:
        self._ensure_parent()
        payload = {
            "version": _VERSION,
            "sessions": {
                session_id: _metadata_to_json(metadata)
                for session_id, metadata in sorted(items.items())
                if not _is_empty(metadata)
            },
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(self._path)
        except OSError as e:
            raise SessionPersistError(f"写入会话 metadata 失败: {self._path}: {e}") from e

    def _ensure_parent(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SessionPersistError(
                f"创建会话 metadata 目录失败: {self._path.parent}: {e}"
            ) from e


def _set_or_delete(
    items: dict[str, SessionMetadata],
    session_id: str,
    metadata: SessionMetadata,
) -> None:
    if _is_empty(metadata):
        items.pop(session_id, None)
    else:
        items[session_id] = metadata


def _is_empty(metadata: SessionMetadata) -> bool:
    return (
        metadata.display_title is None
        and metadata.archived_at is None
        and metadata.approval_mode == "per_request"
        and metadata.approval_mode_version == 0
    )


def _parse_metadata(value: dict[str, Any]) -> SessionMetadata:
    display_title = value.get("display_title")
    archived_at = value.get("archived_at")
    approval_mode = value.get("approval_mode")
    approval_mode_version = value.get("approval_mode_version")
    return SessionMetadata(
        display_title=display_title if isinstance(display_title, str) else None,
        archived_at=_parse_datetime(archived_at) if isinstance(archived_at, str) else None,
        approval_mode=(
            approval_mode if approval_mode in {"per_request", "session_auto"} else "per_request"
        ),
        approval_mode_version=(
            approval_mode_version
            if isinstance(approval_mode_version, int)
            and not isinstance(approval_mode_version, bool)
            and approval_mode_version >= 0
            else 0
        ),
    )


def _metadata_to_json(metadata: SessionMetadata) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if metadata.display_title is not None:
        value["display_title"] = metadata.display_title
    if metadata.archived_at is not None:
        value["archived_at"] = _serialize_datetime(metadata.archived_at)
    if metadata.approval_mode_version > 0 or metadata.approval_mode != "per_request":
        value["approval_mode"] = metadata.approval_mode
        value["approval_mode_version"] = metadata.approval_mode_version
    return value


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _serialize_datetime(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")
