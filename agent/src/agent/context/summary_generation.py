"""Collect a complete summary while the transport can observe upstream progress."""

import logging

from llm_providers.errors import (
    LLMEmptyResponseError,
    LLMIncompleteResponseError,
)
from llm_providers.final_result import final_response_text

from llm_providers import LanguageModel, LLMTurnDone, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


def generate_summary(model: LanguageModel, request: ModelRequest) -> ModelResponse:
    # Only the validated terminal response becomes a summary. Reasoning,
    # commentary and partial text remain private, but streaming lets the
    # transport refresh its idle timeout while the provider is making progress.
    events = model.stream(request)
    trace = request.control.trace_context
    logger.info(
        "summary_generation_budget session=%s run=%s attempt=%s max_tokens=%s",
        trace.session_id if trace else None,
        trace.run_id if trace else None,
        trace.call_index if trace else None,
        request.settings.get("max_tokens"),
    )
    try:
        for event in events:
            if request.control.cancel_check is not None:
                request.control.cancel_check()
            if isinstance(event, LLMTurnDone):
                return ModelResponse(
                    text=final_response_text(event),
                    usage=event.usage,
                    diagnostics=event.diagnostics,
                )
        raise LLMIncompleteResponseError("missing_terminal")
    except (LLMIncompleteResponseError, LLMEmptyResponseError) as exc:
        logger.warning(
            "summary_generation_failed session=%s run=%s max_tokens=%s reason=%s",
            trace.session_id if trace else None,
            trace.run_id if trace else None,
            request.settings.get("max_tokens"),
            getattr(exc, "stop_reason", exc.reason_code),
        )
        raise
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()
