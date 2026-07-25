"""Session persistence invalidation revisions and publication."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import portalocker
from portalocker.exceptions import LockException

from .errors import SessionPersistError

logger = logging.getLogger(__name__)

SessionChangeKind = Literal["created", "content", "metadata", "archived", "unarchived", "deleted"]
SessionChannel = Literal["text", "voice"]


@dataclass(frozen=True)
class SessionMutationContext:
    origin: str | None = None
    channel: SessionChannel | None = None


@dataclass(frozen=True)
class SessionChange:
    session_id: str
    revision: int
    change_kind: SessionChangeKind
    origin: str | None = None
    channel: SessionChannel | None = None


_mutation_context: ContextVar[SessionMutationContext | None] = ContextVar(
    "session_mutation_context", default=None
)


def current_session_mutation_context() -> SessionMutationContext:
    return _mutation_context.get() or SessionMutationContext()


def set_session_mutation_context(
    *, origin: str | None = None, channel: SessionChannel | None = None
) -> Token[SessionMutationContext | None]:
    return _mutation_context.set(SessionMutationContext(origin=origin, channel=channel))


def reset_session_mutation_context(token: Token[SessionMutationContext | None]) -> None:
    _mutation_context.reset(token)


@contextmanager
def session_mutation_context(
    *, origin: str | None = None, channel: SessionChannel | None = None
) -> Iterator[None]:
    token = set_session_mutation_context(origin=origin, channel=channel)
    try:
        yield
    finally:
        reset_session_mutation_context(token)


class SessionRevisionStore:
    """Small locked sidecar that owns per-session monotonic revisions."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")

    @property
    def path(self) -> Path:
        return self._path

    def next_revision(self, session_id: str, *, deleted: bool = False) -> int:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with portalocker.Lock(self._lock_path, "a", encoding="utf-8", timeout=10.0):
                payload = self._read_unlocked()
                sessions = payload.setdefault("sessions", {})
                current = sessions.get(session_id, {})
                revision = int(current.get("revision", 0)) + 1
                sessions[session_id] = {"revision": revision, "deleted": deleted}
                self._write_unlocked(payload)
                return revision
        except LockException as exc:
            raise SessionPersistError(
                f"获取 session revision 文件锁超时: {self._path}: {exc}"
            ) from exc
        except (OSError, ValueError, TypeError) as exc:
            raise SessionPersistError(f"更新 session revision 失败: {self._path}: {exc}") from exc

    def current_revision(self, session_id: str) -> int:
        """Read the durable per-session revision without mutating it."""
        if not self._path.exists():
            return 0
        try:
            with portalocker.Lock(self._lock_path, "a", encoding="utf-8", timeout=10.0):
                payload = self._read_unlocked()
                current = payload.get("sessions", {}).get(session_id, {})
                return int(current.get("revision", 0))
        except LockException as exc:
            raise SessionPersistError(
                f"获取 session revision 文件锁超时: {self._path}: {exc}"
            ) from exc
        except (OSError, ValueError, TypeError) as exc:
            raise SessionPersistError(f"读取 session revision 失败: {self._path}: {exc}") from exc

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "sessions": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("sessions", {}), dict):
            raise ValueError("session revision 文件结构无效")
        return raw

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._path)


class SessionChangeBroker:
    """Records a durable revision and best-effort publishes invalidation."""

    def __init__(
        self,
        revision_store: SessionRevisionStore,
        publisher: Callable[[SessionChange], None] | None = None,
    ) -> None:
        self._revision_store = revision_store
        self._publisher = publisher

    def set_publisher(self, publisher: Callable[[SessionChange], None] | None) -> None:
        self._publisher = publisher

    def current_revision(self, session_id: str) -> int:
        return self._revision_store.current_revision(session_id)

    def record_change(
        self,
        session_id: str,
        *,
        change_kind: SessionChangeKind,
        origin: str | None = None,
        channel: SessionChannel | None = None,
    ) -> SessionChange | None:
        context = current_session_mutation_context()
        try:
            revision = self._revision_store.next_revision(
                session_id, deleted=change_kind == "deleted"
            )
            change = SessionChange(
                session_id=session_id,
                revision=revision,
                change_kind=change_kind,
                origin=origin if origin is not None else context.origin,
                channel=channel if channel is not None else context.channel,
            )
            if self._publisher is not None:
                self._publisher(change)
            return change
        except Exception:
            logger.exception(
                "session change publish failed: session_id=%s kind=%s",
                session_id,
                change_kind,
            )
            return None
