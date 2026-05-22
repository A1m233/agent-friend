"""agent-friend · Agent 引擎。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/。
"""

from .context import BuildResult, ContextManager, NaiveContextManager
from .conversation import MAX_TOOL_TURNS_DEFAULT, Conversation
from .conversation_events import (
    ConversationEvent,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)
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
    NullSessionStore,
    Session,
    SessionCorruptError,
    SessionError,
    SessionManager,
    SessionNotFoundError,
    SessionPersistError,
    SessionStore,
    SessionSummary,
)
from .system_prompt import (
    PersonaSection,
    Section,
    StaticSection,
    SystemPromptComposer,
)
from .tools import (
    Tool,
    ToolDuplicateError,
    ToolError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    make_default_registry,
)

__version__ = "0.1.0"

__all__ = [
    "BUILTIN_DEFAULT_PERSONA_ID",
    "DEFAULT_EXTERNAL_PERSONAS_DIR",
    "KEEP",
    "MAX_TOOL_TURNS_DEFAULT",
    "AgentError",
    "BuildResult",
    "ContextManager",
    "Conversation",
    "ConversationEvent",
    "Event",
    "EventType",
    "JsonlSessionStore",
    "MarkdownPromptBuilder",
    "Message",
    "NaiveContextManager",
    "NullSessionStore",
    "PersonaAlreadyExistsError",
    "PersonaAmbiguousError",
    "PersonaCatalog",
    "PersonaInfo",
    "PersonaNotFoundError",
    "PersonaPersistError",
    "PersonaReadOnlyError",
    "PersonaSection",
    "PromptBuilder",
    "Role",
    "Section",
    "Session",
    "SessionCorruptError",
    "SessionError",
    "SessionManager",
    "SessionNotFoundError",
    "SessionPersistError",
    "SessionStore",
    "SessionSummary",
    "StaticSection",
    "SystemPromptComposer",
    "TextDelta",
    "Tool",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolDuplicateError",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "TurnDone",
    "make_default_registry",
    "random_fallback",
]
