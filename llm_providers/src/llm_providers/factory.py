"""Public construction helpers for the provider-neutral model boundary."""

from __future__ import annotations

from .catalog import ModelPurpose, build_provider_spec
from .client import LLMClient
from .protocol import LanguageModel


def create_language_model(
    *,
    provider_id: str,
    api_key: str,
    model_id: str | None = None,
    purpose: ModelPurpose = "chat",
    context_window: int | None = None,
    timeout_seconds: float | None = None,
) -> LanguageModel:
    """Create the default transport-backed implementation of ``LanguageModel``.

    Callers choose a product provider and model, while ``llm_providers`` keeps
    ownership of ``ProviderSpec`` and the concrete LiteLLM-backed client.
    """

    return LLMClient(
        build_provider_spec(
            provider_id=provider_id,
            api_key=api_key,
            model_id=model_id,
            purpose=purpose,
            context_window=context_window,
            timeout_seconds=timeout_seconds,
        )
    )


__all__ = ["create_language_model"]
