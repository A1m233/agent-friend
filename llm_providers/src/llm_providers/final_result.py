"""Final text consumption, independent of incremental conversation presentation."""

from .errors import LLMEmptyResponseError, LLMIncompleteResponseError
from .stream_events import LLMTurnDone


def final_response_text(done: LLMTurnDone) -> str:
    """Require one unambiguous result; never concatenate commentary into JSON."""

    def invalid(reason: str) -> LLMIncompleteResponseError:
        return LLMIncompleteResponseError(reason, usage=done.usage, diagnostics=done.diagnostics)

    if done.stop_reason != "end_turn":
        raise invalid(done.stop_reason)
    turn = done.assistant_turn
    if turn is None:
        raise invalid("missing_assistant_turn")
    if turn.tool_calls:
        raise invalid("unexpected_tool_calls")
    if turn.text_items:
        finals = [item for item in turn.text_items if item.phase == "final_answer"]
        if finals:
            if len(finals) != 1 or any(item.phase is None for item in turn.text_items):
                raise invalid("ambiguous_final_result")
            text = finals[0].content
        elif len(turn.text_items) == 1 and turn.text_items[0].phase is None:
            text = turn.text_items[0].content
        else:
            raise invalid("missing_or_ambiguous_final_result")
    else:
        text = turn.content or ""
    if not text.strip():
        raise LLMEmptyResponseError("模型未返回最终正文。")
    return text
