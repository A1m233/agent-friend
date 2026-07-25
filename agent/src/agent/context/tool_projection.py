"""tool result 的模型视图安全投影；原始 Message 不做任何改写。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..messages import Message
from .budget import CHARS_TO_TOKENS, estimate_request_tokens, estimate_tokens
from .protocol import RuntimeContext

MIN_TOOL_RESULT_TOKENS = 512
MAX_TOOL_RESULT_TOKENS = 16_000


@dataclass(frozen=True)
class ToolProjectionStats:
    truncated: int = 0
    omitted: int = 0


def _char_budget(token_budget: int) -> int:
    return max(0, math.floor(token_budget / CHARS_TO_TOKENS))


def _project_message(
    message: Message, content: str, *, stage: str, original_tokens: int
) -> Message:
    meta = dict(message.meta)
    meta["context_projection"] = {
        "kind": "tool_result_truncated",
        "stage": stage,
        "original_estimated_tokens": original_tokens,
        "projected_estimated_tokens": int(len(content) * CHARS_TO_TOKENS),
    }
    return replace(message, content=content, meta=meta)


def _truncate_middle(content: str, token_budget: int, original_tokens: int) -> str:
    marker = (
        "\n[tool result truncated for model context; "
        f"original_estimated_tokens={original_tokens}]\n"
    )
    available = max(0, _char_budget(token_budget) - len(marker))
    head = math.ceil(available * 0.6)
    tail = available - head
    suffix = content[-tail:] if tail else ""
    return f"{content[:head]}{marker}{suffix}"


def project_tool_results(
    messages: list[Message],
    runtime: RuntimeContext,
    *,
    emergency: bool = False,
) -> tuple[list[Message], ToolProjectionStats]:
    """限制 tool result 视图，并在必要时用短占位继续释放预算。"""
    threshold = runtime.budget.trigger_threshold
    per_result_cap = max(
        MIN_TOOL_RESULT_TOKENS,
        min(MAX_TOOL_RESULT_TOKENS, max(1, threshold // 4)),
    )
    projected: list[Message] = []
    truncated = 0
    for message in messages:
        original_tokens = estimate_tokens([message])
        if message.role != "tool" or original_tokens <= per_result_cap:
            projected.append(message)
            continue
        content = _truncate_middle(message.content, per_result_cap, original_tokens)
        projected.append(
            _project_message(
                message,
                content,
                stage="per_result_cap",
                original_tokens=original_tokens,
            )
        )
        truncated += 1

    omitted = 0
    if emergency and estimate_request_tokens(projected, runtime) > threshold:
        candidates = sorted(
            (
                (estimate_tokens([message]), index)
                for index, message in enumerate(projected)
                if message.role == "tool"
            ),
            key=lambda item: (-item[0], item[1]),
        )
        for _, index in candidates:
            message = projected[index]
            original_tokens = int(
                message.meta.get("context_projection", {}).get(
                    "original_estimated_tokens", estimate_tokens([message])
                )
            )
            placeholder = (
                "[tool result omitted from model context; "
                f"original_estimated_tokens={original_tokens}; "
                "reason=context_window_budget; raw_result_is_preserved]"
            )
            projected[index] = _project_message(
                message,
                placeholder,
                stage="emergency_placeholder",
                original_tokens=original_tokens,
            )
            omitted += 1
            if estimate_request_tokens(projected, runtime) <= threshold:
                break

    return projected, ToolProjectionStats(truncated=truncated, omitted=omitted)
