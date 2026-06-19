"""agent-friend · Agent 引擎。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/。
"""

from .context import (
    BudgetSnapshot,
    BuildResult,
    CompactionRecord,
    ContextManager,
    FifoContextManager,
    NaiveContextManager,
    PriorSummary,
    RuntimeContext,
    SummarizingContextManager,
    default_context_manager,
)
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
from .paths import (
    cli_history_path,
    log_dir,
    memory_db_path,
    personas_dir,
    sessions_dir,
    user_data_dir,
)
from .personas import (
    BUILTIN_DEFAULT_PERSONA_ID,
    KEEP,
    PersonaCatalog,
    PersonaInfo,
)
from .prompts import (
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
    ChannelSection,
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
    "KEEP",
    "MAX_TOOL_TURNS_DEFAULT",
    "AgentError",
    "BudgetSnapshot",
    "BuildResult",
    "ChannelSection",
    "CompactionRecord",
    "ContextManager",
    "Conversation",
    "ConversationEvent",
    "Event",
    "EventType",
    "FifoContextManager",
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
    "PriorSummary",
    "PromptBuilder",
    "Role",
    "RuntimeContext",
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
    "SummarizingContextManager",
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
    "cli_history_path",
    "default_context_manager",
    "log_dir",
    "make_default_registry",
    "memory_db_path",
    "personas_dir",
    "random_fallback",
    "sessions_dir",
    "user_data_dir",
]
