"""Separate the retained summary size from the cost of generating it."""

from __future__ import annotations

from llm_providers.errors import LLMIncompleteResponseError, LLMOutputBudgetExhaustedError

from llm_providers import get_model_capabilities

from .protocol import RuntimeContext

SUMMARY_GENERATION_TOKENS = 16_384
SUMMARY_RETRY_TOKENS = 32_768


def summary_target_tokens(runtime: RuntimeContext) -> int:
    """Retained-text planning target, independent of reasoning/output allowance."""
    return max(512, min(4096, int(runtime.budget.effective_window * 0.05)))


def summary_generation_tokens(runtime: RuntimeContext, *, expanded: bool = False) -> int:
    # The provider owns admission of the complete summary request. Do not reuse
    # the main loop's trigger threshold or estimate input from character counts.
    ceiling = get_model_capabilities(runtime.llm_client).max_output_tokens
    desired = SUMMARY_RETRY_TOKENS if expanded else SUMMARY_GENERATION_TOKENS
    return max(
        0,
        min(desired, runtime.budget.effective_window, ceiling if ceiling is not None else desired),
    )


def summary_output_exhausted(error: Exception) -> bool:
    return isinstance(error, LLMOutputBudgetExhaustedError) or (
        isinstance(error, LLMIncompleteResponseError)
        and error.stop_reason in {"length", "max_tokens", "max_output_tokens"}
    )
