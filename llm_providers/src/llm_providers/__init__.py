"""agent-friend · LLM Provider 适配层。

详见 README.md 与 docs/requirements/001-foundation-chat-and-memory/design.md §4.1。
"""

from .catalog import (
    CATALOG_REVISION,
    DEEPSEEK_PROVIDER_ID,
    KIMI_API_KEY,
    KIMI_PROVIDER_ID,
    ModelPurpose,
    ModelRouteCapabilities,
    ProviderDefinition,
    ProviderModel,
    all_model_ids,
    build_provider_spec,
    default_model_for_provider,
    infer_provider_id_from_model,
    model_belongs_to_provider,
    model_capabilities,
    normalize_provider_id,
    provider_definition,
    public_model_catalog,
    public_provider_models,
)
from .client import LLMClient
from .errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMContentPolicyError,
    LLMContextWindowError,
    LLMError,
    LLMNetworkError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTransientProviderError,
)
from .multimodal import MultimodalInputAdapter
from .spec import ProviderConfig, ProviderSpec
from .stream_events import (
    LLMRetryScheduled,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
    LLMUsage,
)

__version__ = "0.1.0"

__all__ = [
    "CATALOG_REVISION",
    "DEEPSEEK_PROVIDER_ID",
    "KIMI_API_KEY",
    "KIMI_PROVIDER_ID",
    "LLMAuthError",
    "LLMBadRequestError",
    "LLMClient",
    "LLMContentPolicyError",
    "LLMContextWindowError",
    "LLMError",
    "LLMNetworkError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMRetryScheduled",
    "LLMStreamEvent",
    "LLMTextDelta",
    "LLMToolCallDelta",
    "LLMTransientProviderError",
    "LLMTurnDone",
    "LLMUsage",
    "ModelPurpose",
    "ModelRouteCapabilities",
    "MultimodalInputAdapter",
    "ProviderConfig",
    "ProviderDefinition",
    "ProviderModel",
    "ProviderSpec",
    "all_model_ids",
    "build_provider_spec",
    "default_model_for_provider",
    "infer_provider_id_from_model",
    "model_belongs_to_provider",
    "model_capabilities",
    "normalize_provider_id",
    "provider_definition",
    "public_model_catalog",
    "public_provider_models",
]
