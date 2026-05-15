"""agent-friend · Agent 引擎。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/。
"""

from .context import BuildResult, ContextManager, NaiveContextManager
from .conversation import Conversation
from .errors import (
    AgentError,
    PersonaAlreadyExistsError,
    PersonaAmbiguousError,
    PersonaNotFoundError,
    PersonaPersistError,
    PersonaReadOnlyError,
)
from .fallbacks import random_fallback
from .messages import Message, Role
from .personas import (
    BUILTIN_DEFAULT_PERSONA_ID,
    KEEP,
    PersonaCatalog,
    PersonaInfo,
)
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
    "BUILTIN_DEFAULT_PERSONA_ID",
    "DEFAULT_EXTERNAL_PERSONAS_DIR",
    "KEEP",
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
    "PersonaAlreadyExistsError",
    "PersonaAmbiguousError",
    "PersonaCatalog",
    "PersonaInfo",
    "PersonaNotFoundError",
    "PersonaPersistError",
    "PersonaReadOnlyError",
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
