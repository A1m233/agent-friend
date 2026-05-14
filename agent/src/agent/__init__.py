"""agent-friend · Agent 引擎。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/。
"""

from .context import BuildResult, ContextManager, NaiveContextManager
from .conversation import Conversation
from .errors import AgentError, PersonaNotFoundError
from .fallbacks import random_fallback
from .messages import Message, Role
from .prompts import (
    DEFAULT_EXTERNAL_PERSONAS_DIR,
    MarkdownPromptBuilder,
    PromptBuilder,
)
from .sessions import (
    Event,
    EventType,
    JsonlSessionStore,
    Session,
    SessionCorruptError,
    SessionError,
    SessionManager,
    SessionNotFoundError,
    SessionPersistError,
    SessionStore,
    SessionSummary,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_EXTERNAL_PERSONAS_DIR",
    "AgentError",
    "BuildResult",
    "ContextManager",
    "Conversation",
    "Event",
    "EventType",
    "JsonlSessionStore",
    "MarkdownPromptBuilder",
    "Message",
    "NaiveContextManager",
    "PersonaNotFoundError",
    "PromptBuilder",
    "Role",
    "Session",
    "SessionCorruptError",
    "SessionError",
    "SessionManager",
    "SessionNotFoundError",
    "SessionPersistError",
    "SessionStore",
    "SessionSummary",
    "random_fallback",
]
