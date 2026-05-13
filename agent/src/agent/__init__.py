"""agent-friend · Agent 引擎。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/。
"""

from .context import BuildResult, ContextManager, NaiveContextManager
from .conversation import Conversation
from .messages import Message, Role
from .prompts import MarkdownPromptBuilder, PromptBuilder

__version__ = "0.1.0"

__all__ = [
    "BuildResult",
    "ContextManager",
    "Conversation",
    "MarkdownPromptBuilder",
    "Message",
    "NaiveContextManager",
    "PromptBuilder",
    "Role",
]
