"""``agent.sessions`` —— 会话管理子包。

引擎层一等公民"会话"的完整实现：事件 schema / Session 聚合根 / 持久化协议 +
JSONL 实现 / 业务编排管理器 / 异常体系。

详见 docs/requirements/002-engine-session-management/。
"""

from .catalog import (
    SessionCatalog,
    SessionDescriptor,
    SessionResolution,
    normalize_session_title,
)
from .changes import (
    SessionChange,
    SessionChangeBroker,
    SessionMutationContext,
    SessionRevisionStore,
    current_session_mutation_context,
    session_mutation_context,
)
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
from .metadata import SessionMetadata, SessionMetadataStore
from .session import Session
from .store import (
    JsonlSessionStore,
    NotifyingSessionStore,
    NullSessionStore,
    SessionStore,
    SessionSummary,
)

__all__ = [
    "ALLOWED_EVENT_TYPES",
    "SCHEMA_VERSION",
    "Event",
    "EventType",
    "JsonlSessionStore",
    "LLMClientFactory",
    "NotifyingSessionStore",
    "NullSessionStore",
    "PromptBuilderFactory",
    "Session",
    "SessionCatalog",
    "SessionChange",
    "SessionChangeBroker",
    "SessionCorruptError",
    "SessionDescriptor",
    "SessionError",
    "SessionManager",
    "SessionMetadata",
    "SessionMetadataStore",
    "SessionMutationContext",
    "SessionNotFoundError",
    "SessionPersistError",
    "SessionResolution",
    "SessionRevisionStore",
    "SessionStore",
    "SessionSummary",
    "TitleGenerator",
    "current_session_mutation_context",
    "normalize_session_title",
    "session_mutation_context",
]
