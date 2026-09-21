"""Collect a complete summary while the transport can observe upstream progress."""

from llm_providers.errors import LLMIncompleteResponseError
from llm_providers.final_result import final_response_text

from llm_providers import LanguageModel, LLMTurnDone, ModelRequest, ModelResponse


def generate_summary(model: LanguageModel, request: ModelRequest) -> ModelResponse:
    # Only the validated terminal response becomes a summary. Reasoning,
    # commentary and partial text remain private, but streaming lets the
    # transport refresh its idle timeout while the provider is making progress.
    events = model.stream(request)
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
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()
    raise LLMIncompleteResponseError("missing_terminal")
