"""One durable failure projection for all runtime entry points."""

from agent.runtime.models import RunRequest

from agent import Conversation

from .errors import map_exception


def persist_run_error(conv: Conversation, exc: BaseException, request: RunRequest) -> None:
    if getattr(request.event, "output_visibility", "user") != "user":
        return
    error = map_exception(exc)
    conv.append_run_error_event(
        code=error.code,
        message=error.message,
        recoverable=error.recoverable,
        source=str(request.metadata.get("protocol") or "runtime"),
        run_id=request.run_id,
        sanitize_failed_model_call=error.sanitize_failed_model_call,
        reason=error.reason,
        recovery_action=error.recovery_action,
    )
