"""Non-streaming Responses requests using the cancellable transport lifecycle."""

from ..errors import LLMIncompleteResponseError
from ..final_result import final_response_text
from ..protocol import ModelRequest, ModelResponse
from ..spec import ProviderSpec
from ..stream_events import LLMTurnDone
from .stream import stream_responses


def generate_responses(
    spec: ProviderSpec, request: ModelRequest, *, timeout_seconds: float, attempt: int
) -> ModelResponse:
    events = stream_responses(
        spec,
        request.messages,
        tools=request.tools,
        overrides=dict(request.settings),
        cancel_check=request.control.cancel_check,
        diagnostic_callback=request.control.diagnostic_callback,
        diagnostic_attempt=attempt,
        timeout_seconds=timeout_seconds,
        timeout_policy=request.control.timeout_policy,
        previous_request_witness=request.control.previous_request_witness,
        stream_response=False,
    )
    try:
        for event in events:
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
