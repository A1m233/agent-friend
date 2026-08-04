"""tool result 的模型视图安全投影；原始 Message 不做任何改写。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..messages import Message
from .budget import CHARS_TO_TOKENS, estimate_request_tokens, estimate_tokens
from .protocol import RuntimeContext, ToolContextEditRecord

MIN_TOOL_RESULT_TOKENS = 512
MAX_TOOL_RESULT_TOKENS = 16_000
TOOL_EDIT_HIGH_WATER_RATIO = 0.30
TOOL_EDIT_LOW_WATER_RATIO = 0.15
TOOL_EDIT_HIGH_WATER_MAX_TOKENS = 64_000
TOOL_EDIT_LOW_WATER_MAX_TOKENS = 32_000
TOOL_EDIT_MIN_BATCH_TOKENS = 16_000
TOOL_EDIT_RECENT_RESULT_COUNT = 4
TOOL_CONTEXT_EDIT_POLICY_VERSION = 1

# 040 上线前的事件没有 ``context_retention`` 元数据。只为已知只读、可重放工具
# 提供兼容判定；新事件以工具实现声明并落盘的元数据为准。未知工具默认 pinned。
LEGACY_RECOVERABLE_TOOL_NAMES = frozenset(
    {
        "calculate",
        "datetime_calculate",
        "nearby_place_search",
        "recall_past_chats",
        "recover_tool_result",
        "social_read",
        "social_search",
        "weather_query",
        "web_fetch",
        "web_search",
    }
)


@dataclass(frozen=True)
class ToolProjectionStats:
    truncated: int = 0
    omitted: int = 0
    cleared: int = 0
    cleared_tokens: int = 0
    new_edit: ToolContextEditRecord | None = None
    effective_cleared_result_uuids: tuple[str, ...] = ()


def _char_budget(token_budget: int) -> int:
    return max(0, math.floor(token_budget / CHARS_TO_TOKENS))


def _project_message(
    message: Message, content: str, *, stage: str, original_tokens: int
) -> Message:
    meta = dict(message.meta)
    meta["context_projection"] = {
        "kind": (
            "tool_result_cleared" if stage == "low_water_placeholder" else "tool_result_truncated"
        ),
        "stage": stage,
        "original_estimated_tokens": original_tokens,
        "projected_estimated_tokens": int(len(content) * CHARS_TO_TOKENS),
    }
    return replace(message, content=content, meta=meta)


def _cleared_placeholder(message: Message, original_tokens: int) -> str:
    tool_name = str(message.meta.get("tool_name", "unknown"))
    return (
        "[tool result cleared from active model context; "
        f"tool={tool_name}; result_ref={message.uuid}; "
        f"original_estimated_tokens={original_tokens}; raw_result_is_preserved; "
        "use recover_tool_result with result_ref and a focused query or page only if exact "
        "details are needed]"
    )


def _result_retention(message: Message, *, recovery_available: bool) -> str:
    explicit = message.meta.get("context_retention")
    if not isinstance(explicit, str):
        extra = message.meta.get("extra")
        if isinstance(extra, dict):
            explicit = extra.get("context_retention")
    if explicit == "pinned":
        return "pinned"
    if explicit == "recoverable":
        return "recoverable" if recovery_available else "pinned"
    if not recovery_available:
        return "pinned"
    return (
        "recoverable"
        if message.meta.get("tool_name") in LEGACY_RECOVERABLE_TOOL_NAMES
        else "pinned"
    )


def _tool_result_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens([message]) for message in messages if message.role == "tool")


def _apply_cleared_results(
    messages: list[Message], cleared_result_uuids: set[str]
) -> tuple[list[Message], int, int]:
    projected: list[Message] = []
    cleared = 0
    cleared_tokens = 0
    for message in messages:
        if message.role != "tool" or message.uuid not in cleared_result_uuids:
            projected.append(message)
            continue
        original_tokens = int(
            message.meta.get("context_projection", {}).get(
                "original_estimated_tokens", estimate_tokens([message])
            )
        )
        placeholder = _cleared_placeholder(message, original_tokens)
        projected.append(
            _project_message(
                message,
                placeholder,
                stage="low_water_placeholder",
                original_tokens=original_tokens,
            )
        )
        cleared += 1
        cleared_tokens += max(0, original_tokens - estimate_tokens([projected[-1]]))
    return projected, cleared, cleared_tokens


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
    protect_last_history_turn: bool = True,
    allow_new_edit: bool = True,
    additional_cleared_result_uuids: tuple[str, ...] = (),
) -> tuple[list[Message], ToolProjectionStats]:
    """限制 tool result 视图，并对陈旧可恢复结果执行高/低水位清理。"""
    threshold = runtime.budget.trigger_threshold
    per_result_cap = max(
        MIN_TOOL_RESULT_TOKENS,
        min(MAX_TOOL_RESULT_TOKENS, max(1, threshold // 4)),
    )
    prior_ids = (
        set(runtime.prior_tool_context_edit.cleared_result_uuids)
        if runtime.prior_tool_context_edit is not None
        else set()
    )
    prior_ids.update(additional_cleared_result_uuids)
    projected, cleared, cleared_tokens = _apply_cleared_results(messages, prior_ids)

    capped: list[Message] = []
    truncated = 0
    for message in projected:
        original_tokens = estimate_tokens([message])
        if message.role != "tool" or message.uuid in prior_ids or original_tokens <= per_result_cap:
            capped.append(message)
            continue
        content = _truncate_middle(message.content, per_result_cap, original_tokens)
        capped.append(
            _project_message(
                message,
                content,
                stage="per_result_cap",
                original_tokens=original_tokens,
            )
        )
        truncated += 1
    projected = capped

    new_edit: ToolContextEditRecord | None = None
    if allow_new_edit:
        high_water = min(
            TOOL_EDIT_HIGH_WATER_MAX_TOKENS,
            max(1, int(threshold * TOOL_EDIT_HIGH_WATER_RATIO)),
        )
        low_water = min(
            TOOL_EDIT_LOW_WATER_MAX_TOKENS,
            max(1, int(threshold * TOOL_EDIT_LOW_WATER_RATIO)),
        )
        tokens_before = _tool_result_tokens(projected)
        if tokens_before > high_water:
            protected_indices: set[int] = set()
            tool_indices = [
                index for index, message in enumerate(projected) if message.role == "tool"
            ]
            protected_indices.update(tool_indices[-TOOL_EDIT_RECENT_RESULT_COUNT:])
            if protect_last_history_turn:
                last_user_index = max(
                    (index for index, message in enumerate(projected) if message.role == "user"),
                    default=len(projected),
                )
                protected_indices.update(index for index in tool_indices if index > last_user_index)

            newly_cleared: list[str] = []
            candidate_ids = set(prior_ids)
            candidate_projection = projected
            tokens_after = tokens_before
            for index in tool_indices:
                message = projected[index]
                if (
                    index in protected_indices
                    or message.uuid in candidate_ids
                    or _result_retention(
                        message,
                        recovery_available=runtime.tool_result_recovery_available,
                    )
                    != "recoverable"
                ):
                    continue
                candidate_ids.add(message.uuid)
                newly_cleared.append(message.uuid)
                candidate_projection, _, _ = _apply_cleared_results(projected, set(newly_cleared))
                tokens_after = _tool_result_tokens(candidate_projection)
                if tokens_after <= low_water:
                    break
            batch_cleared_tokens = max(0, tokens_before - tokens_after)
            if newly_cleared and batch_cleared_tokens >= TOOL_EDIT_MIN_BATCH_TOKENS:
                projected = candidate_projection
                cleared += len(newly_cleared)
                cleared_tokens += batch_cleared_tokens
                active_prior_ids = {
                    message.uuid
                    for message in messages
                    if message.role == "tool" and message.uuid in prior_ids
                }
                effective_ids = tuple(
                    message.uuid
                    for message in messages
                    if message.role == "tool"
                    and message.uuid in active_prior_ids.union(newly_cleared)
                )
                new_edit = ToolContextEditRecord(
                    cleared_result_uuids=effective_ids,
                    newly_cleared_result_uuids=tuple(newly_cleared),
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    cleared_tokens=batch_cleared_tokens,
                    high_water_tokens=high_water,
                    low_water_tokens=low_water,
                    policy_version=TOOL_CONTEXT_EDIT_POLICY_VERSION,
                )
                prior_ids.update(newly_cleared)

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

    return projected, ToolProjectionStats(
        truncated=truncated,
        omitted=omitted,
        cleared=cleared,
        cleared_tokens=cleared_tokens,
        new_edit=new_edit,
        effective_cleared_result_uuids=tuple(
            message.uuid
            for message in messages
            if message.role == "tool" and message.uuid in prior_ids
        ),
    )
