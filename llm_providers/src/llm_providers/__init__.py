"""agent-friend · LLM Provider 适配层。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/design.md §4.1。
"""

from .catalog import (
    DEEPSEEK_PROVIDER_ID,
    KIMI_API_KEY,
    KIMI_PROVIDER_ID,
    ModelPurpose,
    ProviderDefinition,
    ProviderModel,
    all_model_ids,
    build_provider_spec,
    default_model_for_provider,
    infer_provider_id_from_model,
    model_belongs_to_provider,
    normalize_provider_id,
    provider_definition,
    public_provider_models,
)
from .client import LLMClient
from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
)
from .spec import ProviderConfig, ProviderSpec
from .stream_events import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
)

__version__ = "0.1.0"

__all__ = [
    "DEEPSEEK_PROVIDER_ID",
    "KIMI_API_KEY",
    "KIMI_PROVIDER_ID",
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
    "ModelPurpose",
    "ProviderConfig",
    "ProviderDefinition",
    "ProviderModel",
    "ProviderSpec",
    "all_model_ids",
    "build_provider_spec",
    "default_model_for_provider",
    "infer_provider_id_from_model",
    "model_belongs_to_provider",
    "normalize_provider_id",
    "provider_definition",
    "public_provider_models",
]
