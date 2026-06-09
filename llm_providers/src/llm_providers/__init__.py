"""agent-friend · LLM Provider 适配层。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/design.md §4.1。
"""

from .client import LLMClient
from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)
from .spec import ProviderSpec
from .stream_events import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
)

__version__ = "0.1.0"

__all__ = [
    "LLMAuthError",
    "LLMBadRequestError",
    "LLMClient",
    "LLMError",
    "LLMNetworkError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMStreamEvent",
    "LLMTextDelta",
    "LLMToolCallDelta",
    "LLMTurnDone",
    "LLMUsage",
    "ProviderSpec",
]
