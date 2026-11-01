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
TOOL_EDIT_RECENT_HISTORY_BATCH_COUNT = 3
TOOL_CONTEXT_EDIT_POLICY_VERSION = 2

# Retention is independent of the per-result ceiling. Replay in chronological
# order makes evictions stable when new history is appended or a session reopens.
TOOL_IMAGE_HISTORY_HIGH = 10
TOOL_IMAGE_HISTORY_LOW = 4
TOOL_IMAGE_HISTORY_HIGH_BYTES = 32 * 1024 * 1024
TOOL_IMAGE_HISTORY_LOW_BYTES = 16 * 1024 * 1024

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
    if message.images:
        content += (
            f"\n[工具图像已从模型上下文移除；result_ref={message.uuid}；"
            "使用 recover_tool_result(mode=images) 恢复原图；当前不可见]"
        )
    meta["context_projection"]["projected_estimated_tokens"] = int(len(content) * CHARS_TO_TOKENS)
    return replace(message, content=content, meta=meta, images=())


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


def _tool_result_batches(messages: list[Message]) -> list[tuple[int, ...]]:
    """按发起工具调用的 assistant 消息聚合结果，无法关联时单条成批。"""
    call_batch_by_id: dict[str, int] = {}
    batches: dict[tuple[str, int], list[int]] = {}
    for index, message in enumerate(messages):
        if message.role == "assistant":
            tool_calls = message.meta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                call_id = tool_call.get("id")
                if isinstance(call_id, str) and call_id:
                    call_batch_by_id[call_id] = index
            continue
        if message.role != "tool":
            continue

        call_id = message.meta.get("tool_call_id")
        assistant_index = (
            call_batch_by_id.get(call_id) if isinstance(call_id, str) and call_id else None
        )
        key = (
            ("assistant", assistant_index)
            if assistant_index is not None
            else ("unmatched_tool", index)
        )
        batches.setdefault(key, []).append(index)
    return [tuple(indices) for indices in batches.values()]


def _recent_historical_tool_indices(
    messages: list[Message], current_turn_indices: set[int]
) -> set[int]:
    """保护当前轮之外最近若干个完整工具批次。"""
    historical_batches = [
        tuple(index for index in batch if index not in current_turn_indices)
        for batch in _tool_result_batches(messages)
    ]
    historical_batches = [batch for batch in historical_batches if batch]
    return {
        index
        for batch in historical_batches[-TOOL_EDIT_RECENT_HISTORY_BATCH_COUNT:]
        for index in batch
    }


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
        if (
            message.role != "tool"
            or message.images
            or message.uuid in prior_ids
            or original_tokens <= per_result_cap
        ):
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
            tool_indices = [
                index for index, message in enumerate(projected) if message.role == "tool"
            ]
            current_turn_indices: set[int] = set()
            if protect_last_history_turn:
                last_user_index = max(
                    (index for index, message in enumerate(projected) if message.role == "user"),
                    default=len(projected),
                )
                current_turn_indices.update(
                    index for index in tool_indices if index > last_user_index
                )
            protected_indices = current_turn_indices.union(
                _recent_historical_tool_indices(projected, current_turn_indices)
            )

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

    projected, image_omissions = bound_tool_images(projected)
    omitted += image_omissions

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


def bound_tool_images(messages: list[Message]) -> tuple[list[Message], int]:
    """Append within a bounded segment, then evict an old prefix as one batch.

    Preserve complete result groups, especially a newly returned dialogue batch.
    This is image eviction, not a generated summary; raw assets remain recoverable.
    """
    projected = list(messages)
    omitted = 0
    retained: list[int] = []
    count = size = 0
    for index, message in enumerate(messages):
        if not message.images:
            continue
        retained.append(index)
        count += len(message.images)
        size += sum(ref.size_bytes for ref in message.images)
        if count <= TOOL_IMAGE_HISTORY_HIGH and size <= TOOL_IMAGE_HISTORY_HIGH_BYTES:
            continue
        while len(retained) > 1 and (
            count > TOOL_IMAGE_HISTORY_LOW or size > TOOL_IMAGE_HISTORY_LOW_BYTES
        ):
            old_index = retained.pop(0)
            old = messages[old_index]
            count -= len(old.images)
            size -= sum(ref.size_bytes for ref in old.images)
            projected[old_index] = _project_message(
                old, old.content, stage="image_budget", original_tokens=estimate_tokens([old])
            )
            omitted += 1

    return projected, omitted
