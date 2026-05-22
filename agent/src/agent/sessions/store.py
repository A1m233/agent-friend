"""会话持久化层。

:class:`SessionStore` 定义抽象协议；:class:`JsonlSessionStore` 是本期默认
JSONL append-only 实现。

详见 docs/requirements/002-engine-session-management/design.md §4.3。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import portalocker
from portalocker.exceptions import LockException

from .errors import (
    SessionCorruptError,
    SessionNotFoundError,
    SessionPersistError,
)
from .events import Event
from .session import Session

_LOCK_TIMEOUT_SECONDS = 10.0
"""``JsonlSessionStore`` 写操作等待 OS advisory 文件锁的最长秒数。

单条 ``append_event`` 实际写盘开销 < 1ms；正常情况下不会逼近这个上限。撞到
意味着有别的进程长时间持锁——按 SessionPersistError 抛出由上层决定如何
重试或降级。"""


@dataclass(frozen=True)
class SessionSummary:
    """``list()`` 返回的轻量摘要。

    仅基于"首行 ``session_meta`` + 文件 mtime"构造，**每文件 O(1)**，
    不读全文件——这是本期"列表性能稳定"的核心保证（详见 design §5 决策 D-2）。

    Attributes:
        session_id: 会话 id。
        title: ``session_meta.payload.initial_title``。**不会显示"当前可变状态"**
            （如当前 persona / 消息数）——这些是事件流派生属性，要显示就得读全文件。
        created_at: 来自首行事件 ``ts``。
        updated_at: 来自文件 mtime，表示"最近一次有事件写入"。
        persona: ``initial_persona``（不展示"当前"，详见 design §1.2）。
        model: ``initial_model``。
    """

    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    persona: str
    model: str


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

    def load(self, session_id: str) -> Session:
        """完整加载会话。

        Raises:
            SessionNotFoundError: 目标文件不存在。
            SessionCorruptError: 首行非 ``session_meta`` 或某行损坏。
        """

    def list(self) -> list[SessionSummary]:
        """列出所有会话摘要，按 ``updated_at`` 倒序。

        实现要求：**仅读每个文件首行 + stat mtime**，不读全文件。首行损坏的
        文件**跳过**（不抛异常、不入结果）。
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

    文件布局：``{base_dir}/{session_id}.jsonl``，每行一个 :class:`Event` 的 JSON
    序列化结果。文件第一行**必须**是 ``session_meta`` 事件。

    每次 ``create`` / ``append_event`` **重新 open/close 文件**（不持有句柄）。
    写操作通过 :mod:`portalocker` 取 OS advisory exclusive 锁，多进程并发写
    同一 ``{session_id}.jsonl`` 时排队进入；锁超时（``_LOCK_TIMEOUT_SECONDS``）
    抛 :class:`SessionPersistError`。

    详见 docs/requirements/006-agent-bridge/design.md §4.5 / R-4.3.2，以及
    docs/requirements/002-engine-session-management/design.md §4.3。
    """

    def __init__(self, base_dir: Path | str) -> None:
        """
        Args:
            base_dir: 会话文件根目录；不存在会自动创建。
        """
        self._base_dir = Path(base_dir)
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SessionPersistError(f"创建 base_dir 失败: {self._base_dir}: {e}") from e

    @property
    def base_dir(self) -> Path:
        """会话文件根目录（只读视图）。"""
        return self._base_dir

    def _path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.jsonl"

    def create(self, session: Session) -> None:
        path = self._path(session.session_id)
        if path.exists():
            raise SessionPersistError(f"会话文件已存在，不允许覆盖: {path}")
        if not session.events or session.events[0].type != "session_meta":
            raise SessionPersistError(
                "Session 必须以 session_meta 事件开头（请用 Session.new 构造）"
            )
        try:
            with portalocker.Lock(path, "w", encoding="utf-8", timeout=_LOCK_TIMEOUT_SECONDS) as f:
                for ev in session.events:
                    f.write(ev.to_jsonl())
                    f.write("\n")
        except LockException as e:
            raise SessionPersistError(
                f"获取会话文件写锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {path}: {e}"
            ) from e
        except OSError as e:
            raise SessionPersistError(f"写入会话文件失败: {path}: {e}") from e

    def append_event(self, session_id: str, event: Event) -> None:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        try:
            with portalocker.Lock(path, "a", encoding="utf-8", timeout=_LOCK_TIMEOUT_SECONDS) as f:
                f.write(event.to_jsonl())
                f.write("\n")
        except LockException as e:
            raise SessionPersistError(
                f"获取会话文件写锁超时 ({_LOCK_TIMEOUT_SECONDS}s): {session_id}: {e}"
            ) from e
        except OSError as e:
            raise SessionPersistError(f"追加事件失败: {path}: {e}") from e

    def load(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        events: list[Event] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        events.append(Event.from_jsonl(line))
                    except SessionCorruptError as e:
                        raise SessionCorruptError(f"会话文件 {path} 第 {lineno} 行损坏: {e}") from e
        except OSError as e:
            raise SessionPersistError(f"读取会话文件失败: {path}: {e}") from e
        return Session.from_events(events)

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
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"会话不存在: {session_id}")
        try:
            path.unlink()
        except OSError as e:
            raise SessionPersistError(f"删除会话文件失败: {path}: {e}") from e

    def latest(self) -> SessionSummary | None:
        summaries = self.list()
        return summaries[0] if summaries else None

    def _read_summary(self, path: Path) -> SessionSummary | None:
        """读取文件首行 + stat mtime 构造摘要；首行损坏则跳过返回 ``None``。"""
        try:
            with path.open("r", encoding="utf-8") as f:
                first_line = f.readline()
            stat = path.stat()
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
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        return SessionSummary(
            session_id=head.uuid,
            title=title,
            created_at=head.ts,
            updated_at=updated_at,
            persona=persona,
            model=model,
        )


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

    def load(self, session_id: str) -> Session:
        raise SessionNotFoundError(f"NullSessionStore 不持有任何会话；无法加载 {session_id!r}")

    def list(self) -> list[SessionSummary]:
        return []

    def delete(self, session_id: str) -> None:
        return None

    def latest(self) -> SessionSummary | None:
        return None
