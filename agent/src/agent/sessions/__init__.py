"""``agent.sessions`` —— 会话管理子包。

引擎层一等公民"会话"的完整实现：事件 schema / Session 聚合根 / 持久化协议 +
JSONL 实现 / 业务编排管理器 / 异常体系。

详见 docs/requirements/002-engine-session-management/。
"""

from .errors import (
    SessionCorruptError,
    SessionError,
    SessionNotFoundError,
    SessionPersistError,
)
from .events import ALLOWED_EVENT_TYPES, SCHEMA_VERSION, Event, EventType
from .manager import (
    LLMClientFactory,
    PromptBuilderFactory,
    SessionManager,
    TitleGenerator,
)
from .session import Session
from .store import JsonlSessionStore, NullSessionStore, SessionStore, SessionSummary

__all__ = [
    "ALLOWED_EVENT_TYPES",
    "SCHEMA_VERSION",
    "Event",
    "EventType",
    "JsonlSessionStore",
    "LLMClientFactory",
    "NullSessionStore",
    "PromptBuilderFactory",
    "Session",
    "SessionCorruptError",
    "SessionError",
    "SessionManager",
    "SessionNotFoundError",
    "SessionPersistError",
    "SessionStore",
    "SessionSummary",
    "TitleGenerator",
]
