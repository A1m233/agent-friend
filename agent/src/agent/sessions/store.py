"""会话持久化层。

:class:`SessionStore` 定义抽象协议；:class:`JsonlSessionStore` 是本期默认
JSONL append-only 实现。

详见 docs/requirements/002-engine-session-management/design.md §4.3。
"""

from __future__ import annotations

import base64
import io
import os
import stat
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import BinaryIO, Protocol, cast

import portalocker
from portalocker.exceptions import LockException

from llm_providers import (
    ModelRuntimeConfig,
    default_model_runtime_config,
    validate_model_runtime_config,
)

from .changes import SessionChangeBroker
from .errors import (
    SessionCorruptError,
    SessionNotFoundError,
    SessionPersistError,
    SessionWriterConflictError,
)
from .events import ROLLOUT_EVENT_TYPES, Event
from .file_observation import observe_session_file
from .query import (
    CancelCheck,
    SessionActivity,
    SessionQueryCache,
    SessionQuerySnapshot,
    SourceVersion,
    check_cancel,
    list_session_activity,
    load_session,
    query_session,
)
from .query_observation import (
    SessionQueryObservation,
    observe_query_maintenance_failure,
    observe_session_query,
)
from .session import Session
from .store_types import SessionLoadDiagnostics

_LOCK_TIMEOUT_SECONDS = 10.0
"""Maximum wait for a session file lock; timeout alone does not identify its owner."""

_REVERSE_READ_CHUNK_BYTES = 8192


class SessionWriterLease(Protocol):
    """Lifetime ownership for one writable resident session."""

    def release(self) -> None:
        """Release the OS writer lease. Safe to call repeatedly."""


class _NullWriterLease:
    def release(self) -> None:
        return None


class _PortalockerWriterLease:
    def __init__(
        self,
        lock: portalocker.Lock,
        handle: object,
        validate: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._lock = lock
        self._handle = handle
        self._validate = validate
        self._on_release = on_release

    def validate(self) -> None:
        if self._handle is None:
            raise SessionWriterConflictError("session writer lease 已释放")
        self._validate()

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._lock.release()
        finally:
            self._handle = None
            self._on_release()


def _iter_lines_reverse(path: Path, cancel_check: CancelCheck = None) -> Iterator[str]:
    """Yield non-empty UTF-8 lines from a file in reverse order."""
    with path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        pending = b""
        while position > 0:
            check_cancel(cancel_check)
            read_size = min(_REVERSE_READ_CHUNK_BYTES, position)
            position -= read_size
            file.seek(position)
            pending = file.read(read_size) + pending
            lines = pending.split(b"\n")
            pending = lines[0]
            for line in reversed(lines[1:]):
                if line.strip():
                    try:
                        yield line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
        if pending.strip():
            try:
                yield pending.decode("utf-8")
            except UnicodeDecodeError:
                return


_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
"""Windows reserved device names, case-insensitive and extension-insensitive."""

_WINDOWS_FORBIDDEN_FILENAME_CHARS = set('<>:"/\\|?*')
"""Characters that cannot appear in a Windows filename component."""

_ENCODED_SESSION_FILENAME_PREFIX = "sid_"
"""Prefix for session ids that need a portable filename encoding."""


@dataclass(frozen=True)
class SessionSummary:
    """``list()`` 返回的轻量摘要。

    基于首行 ``session_meta`` 与从文件尾部找到的最后一条 user/assistant
    message 构造。反向扫描会在命中最近消息后立即停止，不完整加载 Session。

    Attributes:
        session_id: 会话 id。
        title: ``session_meta.payload.initial_title``。**不会显示"当前可变状态"**
            （如当前 persona / 消息数）——这些是事件流派生属性，要显示就得读全文件。
        created_at: 来自首行事件 ``ts``。
        updated_at: 最后一条 user/assistant message 的时间；没有消息时回退
            ``created_at``。控制事件不会改变会话列表活跃时间。
        persona: ``initial_persona``（不展示"当前"，详见 design §1.2）。
    model: ``initial_model``。
        archived_at: 会话管理 metadata 中的归档时间。``None`` 表示未归档。
    """

    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    persona: str
    model: str
    reasoning_preset: str | None = None
    archived_at: datetime | None = None
    approval_mode: str = "per_request"
    approval_mode_version: int = 0
    origin_kind: str | None = None
    origin_id: str | None = None
    origin_occurrence_id: str | None = None


class SessionStore(Protocol):
    """会话持久化协议。

    实现方负责具体存储介质（jsonl 文件 / sqlite / 远程对象存储 / ...）。
    所有方法的失败都用 :class:`SessionError` 子类抛出，**不直接暴露 OSError**。
    """

    def create(self, session: Session) -> None:
        """新建会话，写入首行 ``session_meta`` 事件。

        Args:
            session: 已用 :meth:`Session.new` 构造好的实例（events 至少含
                首行 ``session_meta``）。

        Raises:
            SessionPersistError: 文件已存在（不允许覆盖）或 IO 失败。
        """

    def append_event(self, session_id: str, event: Event) -> None:
        """追加一条事件。

        Raises:
            SessionNotFoundError: 目标文件不存在。
            SessionPersistError: IO 失败。
        """

    def exists(self, session_id: str) -> bool:
        """Check file/session presence without loading or validating its history.

        Only absence returns false. Inability to inspect persistence raises
        SessionPersistError. Success does not establish readable/recoverable history.
        """

    def load(self, session_id: str) -> Session:
        """完整加载会话。

        Raises:
            SessionNotFoundError: 目标文件不存在。
            SessionCorruptError: 首行非 ``session_meta`` 或某行损坏。
        """

    def list(self) -> list[SessionSummary]:
        """列出所有会话摘要，按 ``updated_at`` 倒序。

        实现要求：读取每个文件首行，并从文件尾部反向查找最后一条会话消息；
        不完整构造 Session。首行损坏的文件**跳过**（不抛异常、不入结果）。
        """

    def delete(self, session_id: str) -> None:
        """删除会话（hard delete）。

        Raises:
            SessionNotFoundError: 目标文件不存在。
            SessionPersistError: IO 失败。
        """

    def latest(self) -> SessionSummary | None:
        """最近活跃会话的摘要；无会话则 ``None``。"""


class JsonlSessionStore:
    """JSONL append-only 实现。

    文件布局：``{base_dir}/{portable_filename(session_id)}.jsonl``，每行一个
    :class:`Event` 的 JSON 序列化结果。文件第一行**必须**是 ``session_meta`` 事件。
    安全的历史 ``session_id`` 仍沿用 raw 文件名；包含 ``:`` 等跨平台非法文件名
    字符的 id 会编码成稳定文件名。

    每次 ``create`` / ``append_event`` **重新 open/close 文件**（不持有句柄）。
    写操作通过 :mod:`portalocker` 取 OS advisory exclusive 锁，多进程并发写
    同一会话文件时排队进入；锁超时（``_LOCK_TIMEOUT_SECONDS``）抛
    :class:`SessionPersistError`。

    详见 docs/requirements/006-agent-bridge/design.md §4.5 / R-4.3.2，以及
    docs/requirements/002-engine-session-management/design.md §4.3。
    """

    def __init__(
        self,
        base_dir: Path | str,
        *,
        query_cache_entries: int = 64,
        query_cache_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        """
        Args:
            base_dir: 会话文件根目录；不存在会自动创建。
        """
        self._base_dir = Path(base_dir)
        self._query_cache = SessionQueryCache(
            max_entries=query_cache_entries, max_bytes=query_cache_bytes
        )
        self._summary_cache: OrderedDict[Path, tuple[SourceVersion, SessionSummary]] = OrderedDict()
        self._activity_cache: OrderedDict[Path, tuple[SourceVersion, SessionActivity]] = (
            OrderedDict()
        )
        self._summary_lock = Lock()
        self._writer_versions: dict[str, SourceVersion] = {}
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SessionPersistError(f"创建 base_dir 失败: {self._base_dir}: {e}") from e

    @property
    def base_dir(self) -> Path:
        """会话文件根目录（只读视图）。"""
        return self._base_dir

    def _path(self, session_id: str) -> Path:
        return self._base_dir / f"{_session_filename_stem(session_id)}.jsonl"

    def _legacy_raw_path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.jsonl"

    def _writer_lease_path(self, session_id: str) -> Path:
        return self._base_dir / f".{_session_filename_stem(session_id)}.writer.lock"

    def _candidate_paths(self, session_id: str) -> list[Path]:
        current = self._path(session_id)
        # Only a single raw filename component can be a legacy candidate.
        # Other opaque ids remain available through their portable encoding.
        if (
            not session_id
            or session_id in {".", ".."}
            or any(char in session_id for char in ("/", "\\", "\x00"))
            or Path(session_id).is_absolute()
            or (os.name == "nt" and not _is_portable_raw_filename_stem(session_id))
        ):
            return [current]
        legacy = self._legacy_raw_path(session_id)
        if legacy == current:
            return [current]
        return [current, legacy]

    def _existing_path(self, session_id: str) -> Path | None:
        for path in self._candidate_paths(session_id):
            try:
                if path.exists():
                    return path
            except OSError:
                continue
        return None

    def exists(self, session_id: str) -> bool:
        try:
            base = self._base_dir.resolve()
            for path in self._candidate_paths(session_id):
                if not path.resolve().is_relative_to(base):
                    raise SessionPersistError("会话文件路径超出受管目录")
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise SessionPersistError("会话文件不是普通文件")
                return True
        except (OSError, RuntimeError) as exc:
            raise SessionPersistError("检查会话文件属性失败") from exc
        return False

    def create(self, session: Session) -> None:
        path = self._path(session.session_id)
        existing = self._existing_path(session.session_id)
        if existing is not None:
            raise SessionPersistError(f"会话文件已存在，不允许覆盖: {existing}")
        if not session.events or session.events[0].type != "session_meta":
            raise SessionPersistError(
                "Session 必须以 session_meta 事件开头（请用 Session.new 构造）"
            )
        try:
            with (
                observe_session_file("create", session.session_id) as observation,
                observation.locked(
                    portalocker.Lock(path, "wb", timeout=_LOCK_TIMEOUT_SECONDS)
                ) as f,
            ):
                binary = cast(BinaryIO, f)
                for ev in session.events:
                    observation.byte_count += binary.write(ev.to_jsonl().encode("utf-8"))
                    observation.byte_count += binary.write(b"\n")
                binary.flush()
                os.fsync(binary.fileno())
        except LockException as e:
            raise SessionPersistError(
                f"获取会话文件写锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {path}: {e}"
            ) from e
        except OSError as e:
            raise SessionPersistError(f"写入会话文件失败: {path}: {e}") from e

    def append_event(self, session_id: str, event: Event) -> None:
        self.append_events(session_id, [event])

    def append_events(self, session_id: str, events: list[Event]) -> None:
        if not events:
            return
        path = self._existing_path(session_id)
        if path is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        try:
            # Encode before the OS lock; preserve missing-session/error semantics.
            encoded = [(item.to_jsonl().encode("utf-8") + b"\n") for item in events]
            with (
                observe_session_file("append", session_id) as observation,
                observation.locked(
                    portalocker.Lock(path, "a+b", timeout=_LOCK_TIMEOUT_SECONDS)
                ) as f,
            ):
                binary = cast(BinaryIO, f)
                with observation.phase("boundary"):
                    before = self._handle_version(binary, path)
                    self._validate_writer_version(session_id, before)
                    binary.seek(0, os.SEEK_END)
                    clean_boundary = True
                    if binary.tell():
                        binary.seek(-1, os.SEEK_END)
                        if binary.read(1) != b"\n":
                            clean_boundary = False
                            binary.seek(0, os.SEEK_END)
                            observation.byte_count += binary.write(b"\n")
                    binary.seek(0, os.SEEK_END)
                with observation.phase("write_flush"):
                    for data in encoded:
                        observation.byte_count += binary.write(data)
                    binary.flush()
                with observation.phase("fsync"):
                    os.fsync(binary.fileno())
                with observation.phase("version"):
                    after = self._handle_version(binary, path)
                    if session_id in self._writer_versions:
                        self._writer_versions[session_id] = after
        except LockException as e:
            raise SessionPersistError(
                f"获取会话文件写锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {session_id}: {e}"
            ) from e
        except OSError as e:
            raise SessionPersistError(f"追加事件失败: {path}: {e}") from e
        try:
            self._advance_read_caches(path, session_id, before, after, events, clean_boundary)
        except Exception as exc:
            # Persistence already succeeded. A disposable projection must never
            # report a failed append (which could make its caller append twice).
            self._query_cache.discard(session_id)
            with self._summary_lock:
                self._summary_cache.pop(path, None)
                self._activity_cache.pop(path, None)
            observe_query_maintenance_failure(session_id, exc)

    def _advance_read_caches(
        self,
        path: Path,
        session_id: str,
        before: SourceVersion,
        after: SourceVersion,
        events: list[Event],
        clean_boundary: bool,
    ) -> None:
        # Never hold the file lock while updating an in-process projection.
        cached = self._query_cache.get(session_id, before)
        if clean_boundary and cached is not None and not cached.load_diagnostics.parse_error_count:
            updated = cached.appended(events, after)
            self._query_cache.put(updated, is_current=lambda: self._source_version(path) == after)
        else:
            self._query_cache.discard(session_id)
        with self._summary_lock:
            summary_entry = self._summary_cache.get(path)
            if clean_boundary and summary_entry is not None and summary_entry[0] == before:
                summary = summary_entry[1]
                for event in events:
                    if event.type in {"user_message", "assistant_message"}:
                        summary = replace(summary, updated_at=event.ts)
                    config = self._event_model_config(event)
                    if config is not None:
                        summary = replace(
                            summary, model=config.model_id, reasoning_preset=config.reasoning_preset
                        )
                if self._source_version(path) == after:
                    self._summary_cache[path] = (after, summary)
            activity_entry = self._activity_cache.get(path)
            if clean_boundary and activity_entry is not None and activity_entry[0] == before:
                activity = activity_entry[1]
                for event in events:
                    if event.type in {"user_message", "assistant_message"}:
                        activity = replace(activity, updated_at=event.ts)
                if self._source_version(path) == after:
                    self._activity_cache[path] = (after, activity)

    def acquire_writer_lease(self, session_id: str) -> SessionWriterLease:
        if self._existing_path(session_id) is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        path = self._writer_lease_path(session_id)
        lock = portalocker.Lock(
            path,
            "a",
            encoding="utf-8",
            timeout=0.0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            handle = lock.acquire()
        except LockException as exc:
            raise SessionWriterConflictError(
                f"session 已由另一个 Bridge 持有写入 lease: {session_id}"
            ) from exc
        try:
            source = self._existing_path(session_id)
            if source is None:
                raise SessionNotFoundError(f"会话不存在: {session_id}")
            self._writer_versions[session_id] = self._source_version(source)
        except BaseException:
            lock.release()
            raise

        def validate() -> None:
            try:
                actual = self._source_version(source)
            except SessionNotFoundError as exc:
                # This is an invalid resident writer, not an absent cold session
                # that an execution adapter may safely auto-create.
                raise SessionWriterConflictError("驻留会话文件已删除，不能自动重建") from exc
            self._validate_writer_version(session_id, actual)

        def released() -> None:
            self._writer_versions.pop(session_id, None)

        return _PortalockerWriterLease(lock, handle, validate, released)

    def _validate_writer_version(self, session_id: str, actual: SourceVersion) -> None:
        expected = self._writer_versions.get(session_id)
        if expected is not None and expected != actual:
            raise SessionWriterConflictError(
                f"驻留会话文件已被外部修改，拒绝继续使用旧运行态: {session_id}"
            )

    def delete_writer_lease(self, session_id: str) -> None:
        path = self._writer_lease_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SessionPersistError(f"删除 session writer lease 失败: {path}: {exc}") from exc

    def load(self, session_id: str, *, cancel_check: CancelCheck = None) -> Session:
        records, diagnostics, version, _count = self._read_records(
            session_id, cancel_check=cancel_check
        )
        session = Session.from_events([event for _, event in records])
        session.load_diagnostics = diagnostics
        session.loaded_source_version = version
        return session

    def can_reuse_loaded(self, session: Session) -> bool:
        path = self._existing_path(session.session_id)
        return (
            session.loaded_source_version is not None
            and path is not None
            and session.loaded_source_version == self._source_version(path)
        )

    def cancellable_load(self, session_id: str, *, cancel_check: CancelCheck = None) -> Session:
        return self.load(session_id, cancel_check=cancel_check)

    @staticmethod
    def _version(info: os.stat_result) -> SourceVersion:
        # Windows stat reports legacy creation-time ctime, while fstat reports
        # change time on current Python. birthtime has identical semantics in both.
        identity_time = getattr(info, "st_birthtime_ns", info.st_ctime_ns)
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity_time)

    def _source_version(self, path: Path) -> SourceVersion:
        try:
            return self._version(path.stat())
        except FileNotFoundError as exc:
            raise SessionNotFoundError("会话文件已删除") from exc
        except OSError as exc:
            raise SessionPersistError("读取会话文件属性失败") from exc

    def _handle_version(self, handle: BinaryIO, path: Path) -> SourceVersion:
        try:
            return self._version(os.fstat(handle.fileno()))
        except io.UnsupportedOperation:
            # File-like test adapters have no OS descriptor.
            return self._source_version(path)

    @contextmanager
    def _read_lock(self, path: Path, cancel_check: CancelCheck) -> Iterator[BinaryIO]:
        if cancel_check is None:
            with portalocker.Lock(
                path,
                "rb",
                flags=portalocker.LOCK_SH | portalocker.LOCK_NB,
                timeout=_LOCK_TIMEOUT_SECONDS,
            ) as handle:
                yield cast(BinaryIO, handle)
            return
        # Short acquisition attempts let cancellation interrupt a contended writer.
        deadline = monotonic() + _LOCK_TIMEOUT_SECONDS
        lock = portalocker.Lock(
            path,
            "rb",
            flags=portalocker.LOCK_SH | portalocker.LOCK_NB,
            timeout=0.05,
            check_interval=0.01,
        )
        while True:
            check_cancel(cancel_check)
            try:
                handle = lock.acquire()
                break
            except LockException:
                if monotonic() >= deadline:
                    raise
        try:
            check_cancel(cancel_check)
            yield cast(BinaryIO, handle)
        finally:
            lock.release()

    def _read_records(
        self,
        session_id: str,
        *,
        query_only: bool = False,
        cancel_check: CancelCheck = None,
    ) -> tuple[list[tuple[int, Event]], SessionLoadDiagnostics, SourceVersion, int]:
        path = self._existing_path(session_id)
        if path is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        records: list[tuple[int, Event]] = []
        parsed_records: list[tuple[int, Event]] = []
        parse_errors: list[tuple[int, int]] = []
        event_count = 0
        try:
            with (
                observe_session_file("load", session_id) as observation,
                ExitStack() as snapshot_handles,
            ):
                chunks: Iterable[bytes]
                snapshot: BinaryIO | None = None
                with observation.locked(self._read_lock(path, cancel_check)) as f:
                    binary = cast(BinaryIO, f)
                    version = self._handle_version(binary, path)
                    try:
                        descriptor = os.dup(binary.fileno())
                    except io.UnsupportedOperation:
                        # Adapters without a descriptor need an owned snapshot
                        # while locked. Real files stream a pinned prefix.
                        chunks = list(self._read_snapshot_bytes(binary, version[2], cancel_check))
                    else:
                        try:
                            snapshot = os.fdopen(descriptor, "rb")
                        except BaseException:
                            os.close(descriptor)
                            raise
                        snapshot_handles.enter_context(snapshot)
                if snapshot is not None:
                    chunks = self._read_snapshot_bytes(snapshot, version[2], cancel_check)
                # Read and parse incrementally outside the file lock. Keep the
                # descriptor alive through the last parsed record.
                with observation.parsing(), observation.phase("stream_read_parse"):
                    for lineno, line in enumerate(self._snapshot_lines(chunks), start=1):
                        check_cancel(cancel_check)
                        observation.byte_count += len(line)
                        if not line.strip():
                            continue
                        try:
                            event = Event.from_jsonl(line.decode("utf-8"))
                        except (UnicodeDecodeError, SessionCorruptError):
                            parse_errors.append((lineno, len(line)))
                            continue
                        parsed_records.append(
                            (
                                lineno,
                                Event(
                                    type=event.type,
                                    uuid=event.uuid,
                                    ts=event.ts,
                                    payload={
                                        "starts_after_corruption": event.payload.get(
                                            "starts_after_corruption"
                                        )
                                    },
                                ),
                            )
                        )
                        if not query_only or event.type not in ROLLOUT_EVENT_TYPES:
                            records.append((event_count, event))
                        event_count += 1
                    diagnostics = self._validate_recoverable_errors(
                        path, parsed_records, parse_errors
                    )
                    Session.from_events([records[0][1]] if records else [])
        except LockException as e:
            raise SessionPersistError(
                f"获取会话文件读锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {path}: {e}"
            ) from e
        except OSError as e:
            raise SessionPersistError(f"读取会话文件失败: {path}: {e}") from e
        check_cancel(cancel_check)
        return records, diagnostics, version, event_count

    @staticmethod
    def _read_snapshot_bytes(
        binary: BinaryIO, length: int, cancel_check: CancelCheck
    ) -> Iterator[bytes]:
        remaining = length
        while remaining:
            check_cancel(cancel_check)
            chunk = binary.read(min(1024 * 1024, remaining))
            if not chunk:
                raise SessionPersistError("会话快照在读取完成前被截断")
            yield chunk
            remaining -= len(chunk)

    @staticmethod
    def _snapshot_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
        """Preserve splitlines semantics, including CRLF across chunk boundaries."""
        pending = b""
        for chunk in chunks:
            lines = (pending + chunk).splitlines(keepends=True)
            pending = b""
            if lines and (not lines[-1].endswith(b"\n")):
                pending = lines.pop()
            yield from lines
        if pending:
            yield pending

    def query(self, session_id: str, *, cancel_check: CancelCheck = None) -> SessionQuerySnapshot:
        with observe_session_query(session_id) as observation:
            return self._query_snapshot(session_id, cancel_check, observation)

    def _query_snapshot(
        self,
        session_id: str,
        cancel_check: CancelCheck,
        observation: SessionQueryObservation,
    ) -> SessionQuerySnapshot:
        check_cancel(cancel_check)
        path = self._existing_path(session_id)
        if path is None:
            observation.miss_reason = "missing"
            self._query_cache.discard(session_id)
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        cached = self._query_cache.get(
            session_id, self._source_version(path), observation=observation
        )
        if cached is not None:
            observation.action = "hit"
            check_cancel(cancel_check)
            return cached
        observation.action = "waiting"
        with self._query_cache.building(session_id, cancel_check, observation=observation):
            cached = self._query_cache.get(
                session_id, self._source_version(path), observation=observation
            )
            if cached is not None:
                observation.action = "coalesced"
                check_cancel(cancel_check)
                return cached
            observation.action = "rebuilt"
            records, diagnostics, version, count = self._read_records(
                session_id, query_only=True, cancel_check=cancel_check
            )
            snapshot = SessionQuerySnapshot.build(
                records,
                raw_event_count=count,
                diagnostics=diagnostics,
                source_version=version,
                cancel_check=cancel_check,
            )
            check_cancel(cancel_check)
            self._query_cache.put(
                snapshot,
                is_current=lambda: self._source_version(path) == version,
                observation=observation,
            )
            # The caller receives one consistent version even when an append won
            # the race. The next query validates the source and reads the new one.
            return snapshot

    @staticmethod
    def _validate_recoverable_errors(
        path: Path,
        parsed_records: list[tuple[int, Event]],
        parse_errors: list[tuple[int, int]],
    ) -> SessionLoadDiagnostics:
        if not parse_errors:
            return SessionLoadDiagnostics()
        first_error_line = min(line for line, _ in parse_errors)
        last_error_line = max(line for line, _ in parse_errors)
        has_commit_before_error = any(
            line < first_error_line and event.type == "rollout_commit"
            for line, event in parsed_records
        )
        records_after_error = [event for line, event in parsed_records if line > last_error_line]
        handled = bool(
            records_after_error
            and records_after_error[0].type == "rollout_segment"
            and records_after_error[0].payload.get("starts_after_corruption") is True
        )
        if not has_commit_before_error:
            raise SessionCorruptError(
                f"会话文件 {path} 在任何 rollout commit 之前损坏，无法安全恢复"
            )
        if records_after_error and not handled:
            raise SessionCorruptError(
                f"会话文件 {path} committed chain 中间损坏，且没有恢复 segment"
            )
        return SessionLoadDiagnostics(
            parse_error_count=len(parse_errors),
            malformed_bytes=sum(size for _, size in parse_errors),
            last_error_line=last_error_line,
            unhandled_tail_corruption=not handled,
        )

    def list(self) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        try:
            files = sorted(self._base_dir.glob("*.jsonl"))
        except OSError as e:
            raise SessionPersistError(f"列出 base_dir 失败: {self._base_dir}: {e}") from e

        for path in files:
            summary = self._read_summary(path)
            if summary is not None:
                summaries.append(summary)

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def delete(self, session_id: str) -> None:
        path = self._existing_path(session_id)
        if path is None:
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        try:
            path.unlink()
            self._query_cache.discard(session_id)
            with self._summary_lock:
                self._summary_cache.pop(path, None)
                self._activity_cache.pop(path, None)
        except OSError as e:
            raise SessionPersistError(f"删除会话文件失败: {path}: {e}") from e

    def latest(self) -> SessionSummary | None:
        summaries = self.list()
        return summaries[0] if summaries else None

    def _read_summary(self, path: Path) -> SessionSummary | None:
        try:
            version = self._source_version(path)
        except (SessionNotFoundError, SessionPersistError):
            return None
        with self._summary_lock:
            cached = self._summary_cache.get(path)
            if cached is not None and cached[0] == version:
                self._summary_cache.move_to_end(path)
                return cached[1]
        summary = self._read_uncached_summary(path)
        if summary is not None:
            with self._summary_lock:
                try:
                    current = self._source_version(path)
                except (SessionNotFoundError, SessionPersistError):
                    return None
                if current == version:
                    self._summary_cache[path] = (version, summary)
                    while len(self._summary_cache) > 512:
                        self._summary_cache.popitem(last=False)
        return summary

    def list_activity(
        self,
        *,
        session_ids: Iterable[str] | None = None,
        exclude_session_ids: Iterable[str] = (),
        cancel_check: CancelCheck = None,
    ) -> tuple[SessionActivity, ...]:
        selected = set(session_ids) if session_ids is not None else None
        excluded = set(exclude_session_ids)
        excluded_paths = {path for sid in excluded for path in self._candidate_paths(sid)}
        try:
            if selected is None:
                paths = sorted(self._base_dir.glob("*.jsonl"))
            else:
                paths = []
                for sid in sorted(selected - excluded):
                    check_cancel(cancel_check)
                    path = self._existing_path(sid)
                    if path is not None:
                        paths.append(path)
            result: list[SessionActivity] = []
            for path in paths:
                check_cancel(cancel_check)
                if path in excluded_paths:
                    continue
                try:
                    version = self._source_version(path)
                except (SessionNotFoundError, SessionPersistError):
                    continue
                with self._summary_lock:
                    cached = self._activity_cache.get(path)
                if cached is not None and cached[0] == version:
                    activity = cached[1]
                else:
                    try:
                        with path.open("r", encoding="utf-8") as handle:
                            head = Event.from_jsonl(handle.readline())
                    except (OSError, UnicodeDecodeError, SessionCorruptError):
                        continue
                    if head.type != "session_meta" or any(
                        not isinstance(head.payload.get(key), str)
                        for key in ("initial_title", "initial_persona", "initial_model")
                    ):
                        continue
                    if head.uuid in excluded or (
                        selected is not None and head.uuid not in selected
                    ):
                        continue
                    activity = SessionActivity(
                        head.uuid,
                        head.ts,
                        self._last_conversation_event_at(
                            path, fallback=head.ts, cancel_check=cancel_check
                        ),
                    )
                    with self._summary_lock:
                        try:
                            current = self._source_version(path)
                        except (SessionNotFoundError, SessionPersistError):
                            continue
                        if current == version:
                            self._activity_cache[path] = (version, activity)
                            while len(self._activity_cache) > 512:
                                self._activity_cache.popitem(last=False)
                if activity.session_id not in excluded and (
                    selected is None or activity.session_id in selected
                ):
                    result.append(activity)
            check_cancel(cancel_check)
            return tuple(sorted(result, key=lambda item: item.updated_at, reverse=True))
        except OSError as exc:
            raise SessionPersistError("枚举会话活动失败") from exc

    def _read_uncached_summary(self, path: Path) -> SessionSummary | None:
        """读取首行与最后会话消息构造摘要；首行损坏则跳过返回 ``None``。"""
        try:
            with path.open("r", encoding="utf-8") as f:
                first_line = f.readline()
        except OSError:
            return None
        if not first_line.strip():
            return None
        try:
            head = Event.from_jsonl(first_line)
        except SessionCorruptError:
            return None
        if head.type != "session_meta":
            return None
        payload = head.payload
        title = payload.get("initial_title")
        persona = payload.get("initial_persona")
        model = payload.get("initial_model")
        if not isinstance(title, str) or not isinstance(persona, str) or not isinstance(model, str):
            return None
        updated_at = self._last_conversation_event_at(path, fallback=head.ts)
        initial_reasoning_preset = (
            str(payload["initial_reasoning_preset"])
            if isinstance(payload.get("initial_reasoning_preset"), str)
            else None
        )
        runtime_config = self._latest_model_runtime_config(
            path,
            initial_model=model,
            initial_reasoning_preset=initial_reasoning_preset,
        )
        return SessionSummary(
            session_id=head.uuid,
            title=title,
            created_at=head.ts,
            updated_at=updated_at,
            persona=persona,
            model=runtime_config.model_id,
            reasoning_preset=runtime_config.reasoning_preset,
            origin_kind=(
                str(payload["origin_kind"]) if isinstance(payload.get("origin_kind"), str) else None
            ),
            origin_id=(
                str(payload["origin_id"]) if isinstance(payload.get("origin_id"), str) else None
            ),
            origin_occurrence_id=(
                str(payload["origin_occurrence_id"])
                if isinstance(payload.get("origin_occurrence_id"), str)
                else None
            ),
        )

    @staticmethod
    def _last_conversation_event_at(
        path: Path, *, fallback: datetime, cancel_check: CancelCheck = None
    ) -> datetime:
        """Return the newest raw user/assistant timestamp without loading the session.

        JSONL is scanned backwards in chunks and stops at the first conversational
        event. A concurrently appended partial line or an older corrupt line is
        ignored for summary projection; full ``load()`` remains strict.
        """
        try:
            for line in _iter_lines_reverse(path, cancel_check):
                try:
                    event = Event.from_jsonl(line)
                except SessionCorruptError:
                    continue
                if event.type in {"user_message", "assistant_message"}:
                    return event.ts
        except OSError:
            return fallback
        return fallback

    @staticmethod
    def _event_model_config(event: Event) -> ModelRuntimeConfig | None:
        if event.type == "model_config_change":
            target = event.payload.get("to")
            if not isinstance(target, dict):
                return None
            model, preset = target.get("model"), target.get("reasoningPreset")
            if not isinstance(model, str) or not isinstance(preset, str):
                return None
            try:
                return validate_model_runtime_config(ModelRuntimeConfig(model, preset))
            except ValueError:
                try:
                    return default_model_runtime_config(model)
                except ValueError:
                    return None
        if event.type == "model_change":
            target = event.payload.get("to")
            if isinstance(target, str):
                try:
                    return default_model_runtime_config(target)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _latest_model_runtime_config(
        path: Path,
        *,
        initial_model: str,
        initial_reasoning_preset: str | None,
    ) -> ModelRuntimeConfig:
        """Project the newest atomic model config without loading the full session."""
        try:
            initial_default = default_model_runtime_config(initial_model)
        except ValueError:
            initial_default = ModelRuntimeConfig(initial_model, "none")
        initial = ModelRuntimeConfig(
            initial_model,
            initial_reasoning_preset or initial_default.reasoning_preset,
        )
        try:
            initial = validate_model_runtime_config(initial)
        except ValueError:
            initial = initial_default

        try:
            for line in _iter_lines_reverse(path):
                try:
                    event = Event.from_jsonl(line)
                except SessionCorruptError:
                    continue
                if event.type == "model_config_change":
                    target = event.payload.get("to")
                    if not isinstance(target, dict):
                        continue
                    model = target.get("model")
                    preset = target.get("reasoningPreset")
                    if not isinstance(model, str) or not isinstance(preset, str):
                        continue
                    try:
                        return validate_model_runtime_config(ModelRuntimeConfig(model, preset))
                    except ValueError:
                        try:
                            return default_model_runtime_config(model)
                        except ValueError:
                            continue
                if event.type == "model_change":
                    target = event.payload.get("to")
                    if isinstance(target, str):
                        try:
                            return default_model_runtime_config(target)
                        except ValueError:
                            continue
        except OSError:
            return initial
        return initial


class NullSessionStore:
    """无副作用 :class:`SessionStore` 实现，用于"会话只在内存中存在"的场景。

    所有写方法都是 no-op；查询方法返回"空"语义：

    - :meth:`create` / :meth:`append_event` / :meth:`delete` 直接返回，不抛异常
    - :meth:`load` 抛 :class:`SessionNotFoundError`（没有任何文件可加载）
    - :meth:`list` 返回空列表；:meth:`latest` 返回 ``None``

    典型场景：``agent-bridge`` 实现 OpenAI ChatCompletion **默认无状态**语义时，
    客户端每次发送完整 messages 历史，bridge 进程内为这一次请求构造 in-memory
    :class:`Session` + :class:`Conversation` 跑一轮、丢弃。bridge 不应在磁盘上
    留下任何 jsonl 文件 —— 注入本 store 即可让 :class:`Conversation` 的所有
    ``append_event`` 调用无副作用，同时保持 :class:`Conversation` 自身的代码
    路径完全不变（不需要 ``if self._store is None`` 守卫）。

    详见 docs/requirements/006-agent-bridge/design.md §4.4（SessionBridge 双语义）
    与 §5.2 N-1 决策。

    Note:
        本实现满足 :class:`SessionStore` Protocol 的全部方法签名，因此可作为
        :class:`SessionManager` / :class:`Conversation` 的 ``store`` 参数直接注入，
        无需任何额外适配。
    """

    def create(self, session: Session) -> None:
        return None

    def append_event(self, session_id: str, event: Event) -> None:
        return None

    def append_events(self, session_id: str, events: list[Event]) -> None:
        return None

    def acquire_writer_lease(self, session_id: str) -> SessionWriterLease:
        return _NullWriterLease()

    def delete_writer_lease(self, session_id: str) -> None:
        return None

    def exists(self, session_id: str) -> bool:
        return False

    def load(self, session_id: str) -> Session:
        raise SessionNotFoundError(f"NullSessionStore 不持有任何会话；无法加载 {session_id!r}")

    def list(self) -> list[SessionSummary]:
        return []

    def delete(self, session_id: str) -> None:
        return None

    def latest(self) -> SessionSummary | None:
        return None


class NotifyingSessionStore:
    """Persistent store decorator that invalidates after successful mutations."""

    def __init__(self, inner: SessionStore, broker: SessionChangeBroker) -> None:
        self._inner = inner
        self._broker = broker

    @property
    def inner(self) -> SessionStore:
        return self._inner

    def create(self, session: Session) -> None:
        self._inner.create(session)
        self._broker.record_change(session.session_id, change_kind="created")

    def append_event(self, session_id: str, event: Event) -> None:
        self._inner.append_event(session_id, event)
        self._broker.record_change(session_id, change_kind="content")

    def append_events(self, session_id: str, events: list[Event]) -> None:
        append_session_events(self._inner, session_id, events)
        if events:
            self._broker.record_change(session_id, change_kind="content")

    def acquire_writer_lease(self, session_id: str) -> SessionWriterLease:
        return acquire_session_writer_lease(self._inner, session_id)

    def delete_writer_lease(self, session_id: str) -> None:
        delete_session_writer_lease(self._inner, session_id)

    def exists(self, session_id: str) -> bool:
        return self._inner.exists(session_id)

    def load(self, session_id: str) -> Session:
        return self._inner.load(session_id)

    def cancellable_load(self, session_id: str, *, cancel_check: CancelCheck = None) -> Session:
        return load_session(self._inner, session_id, cancel_check=cancel_check)

    def can_reuse_loaded(self, session: Session) -> bool:
        method = getattr(self._inner, "can_reuse_loaded", None)
        return bool(method(session)) if callable(method) else False

    def query(self, session_id: str, *, cancel_check: CancelCheck = None) -> SessionQuerySnapshot:
        return query_session(self._inner, session_id, cancel_check=cancel_check)

    def list_activity(
        self,
        *,
        session_ids: Iterable[str] | None = None,
        exclude_session_ids: Iterable[str] = (),
        cancel_check: CancelCheck = None,
    ) -> tuple[SessionActivity, ...]:
        return list_session_activity(
            self._inner,
            session_ids=session_ids,
            exclude_session_ids=exclude_session_ids,
            cancel_check=cancel_check,
        )

    def list(self) -> list[SessionSummary]:
        return self._inner.list()

    def delete(self, session_id: str) -> None:
        self._inner.delete(session_id)
        self._broker.record_change(session_id, change_kind="deleted")

    def latest(self) -> SessionSummary | None:
        return self._inner.latest()


def append_session_events(store: SessionStore, session_id: str, events: list[Event]) -> None:
    """Use the optional batch/fsync capability without widening SessionStore."""

    method = getattr(store, "append_events", None)
    if callable(method):
        method(session_id, events)
        return
    for event in events:
        store.append_event(session_id, event)


def acquire_session_writer_lease(
    store: SessionStore,
    session_id: str,
) -> SessionWriterLease:
    """Acquire a lifetime lease when supported; transient stores remain no-op."""

    method = getattr(store, "acquire_writer_lease", None)
    if callable(method):
        return cast(SessionWriterLease, method(session_id))
    return _NullWriterLease()


def delete_session_writer_lease(store: SessionStore, session_id: str) -> None:
    method = getattr(store, "delete_writer_lease", None)
    if callable(method):
        method(session_id)


def _session_filename_stem(session_id: str) -> str:
    """Return a portable filename stem for a logical session id.

    Existing safe ids keep their historical raw filename for compatibility.
    Unsafe ids are URL-safe base64 encoded so the logical id can still contain
    protocol separators such as ``im:qq:<openid>``.
    """
    if _is_portable_raw_filename_stem(session_id):
        return session_id
    encoded = base64.urlsafe_b64encode(session_id.encode("utf-8")).decode("ascii")
    return f"{_ENCODED_SESSION_FILENAME_PREFIX}{encoded.rstrip('=')}"


def _is_portable_raw_filename_stem(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    if value[-1] in {" ", "."}:
        return False
    if any(ord(ch) < 32 or ch in _WINDOWS_FORBIDDEN_FILENAME_CHARS for ch in value):
        return False
    base = value.split(".", 1)[0].upper()
    return base not in _WINDOWS_RESERVED_FILENAMES
