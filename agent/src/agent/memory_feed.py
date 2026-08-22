"""会话事件 → 记忆素材的投影层（过滤策略**单点维护**处）。

记忆模块只认识"干净的对话发言"（:class:`memory.ConversationFragment` /
:class:`memory.Utterance`），**不认识** :class:`agent.sessions.Event`。本模块是
两者之间唯一的转换点：决定"哪些事件算记忆素材、怎么洗"。新增事件类型 / 调整
过滤口径时**只改这一个函数**，记忆模块不动。

依赖方向：``agent`` 拥有 ``Event`` taxonomy，依赖 ``memory`` 的契约类型产出
``Utterance``；``memory`` 不反向依赖 ``agent``。

详见 docs/requirements/008-engine-memory/design.md §7。
"""

from __future__ import annotations

import logging

from memory import (
    ConversationFragment,
    SemanticUpdateContext,
    SemanticUpdateTurn,
    Utterance,
)

from .context import resolve_prior_summary
from .sessions import Event, Session

__all__ = ["project_post_turn_observation", "project_turn"]

logger = logging.getLogger(__name__)

DEFAULT_SEMANTIC_UPDATE_CONTEXT_TURNS = 10


def project_turn(
    events: list[Event],
    *,
    session_id: str,
    persona_id: str,
    owner_user_id: str = "local",
) -> ConversationFragment:
    """把一段会话事件投影成可抽取的 :class:`ConversationFragment`。

    Args:
        events: 待投影的事件（通常是刚结束这一轮 append 进 session 的那几条）。
        session_id: 来源会话 id（用于拼 ``source_ref``）。
        persona_id: 本段归属 persona。
        owner_user_id: 多 user 预留维度，v1 固定。

    Returns:
        只含 user / assistant 文本发言的 fragment。

    过滤策略（v1）:
        - **保留** ``user_message``、``assistant_message``（``partial=False``）的文本。
        - **丢弃** ``session_meta`` / ``persona_change`` / ``model_change`` /
          ``tool_call_request`` / ``tool_call_result``。
        - ``tool_call_result`` 是最明显的**未来例外**：工具查出的事实可能值得记，
          届时在此处加"洗出有用部分"的逻辑即可。
    """
    utterances = [
        utterance
        for event in events
        if (utterance := _project_utterance(event, session_id=session_id)) is not None
    ]
    return ConversationFragment(
        session_id=session_id,
        utterances=utterances,
        persona_id=persona_id,
        owner_user_id=owner_user_id,
    )


def project_post_turn_observation(
    session: Session,
    *,
    turn_start_idx: int,
    persona_id: str,
    owner_user_id: str = "local",
    max_context_turns: int = DEFAULT_SEMANTIC_UPDATE_CONTEXT_TURNS,
) -> ConversationFragment:
    """Freeze one target fragment plus its prior raw-tail Semantic context."""

    if max_context_turns <= 0:
        raise ValueError("max_context_turns must be positive")

    target_events = list(session.events[turn_start_idx:])
    target = project_turn(
        target_events,
        session_id=session.session_id,
        persona_id=persona_id,
        owner_user_id=owner_user_id,
    )
    if target.is_empty():
        return target

    active_events = list(session.active_events)
    target_event_uuids = {utterance.source_ref.partition("#")[2] for utterance in target.utterances}
    target_start = next(
        (index for index, event in enumerate(active_events) if event.uuid in target_event_uuids),
        None,
    )
    if target_start is None:
        logger.info(
            "semantic update context unavailable session=%s reason=target_not_active",
            session.session_id,
        )
        return target

    resolution = resolve_prior_summary(active_events, session.llm_messages)
    covered_uuid = (
        resolution.prior_summary.covered_through_uuid
        if resolution.prior_summary is not None
        else None
    )
    candidate_start = 0
    if covered_uuid is not None:
        covered_index = next(
            (index for index, event in enumerate(active_events) if event.uuid == covered_uuid),
            None,
        )
        if covered_index is not None and covered_index < target_start:
            candidate_start = covered_index + 1

    candidate_turns = _complete_conversation_turns(
        active_events[candidate_start:target_start],
        session_id=session.session_id,
    )
    selected_turns = candidate_turns[-max_context_turns:]
    omitted_turns = candidate_turns[: -len(selected_turns)] if selected_turns else candidate_turns
    candidate_first_ref = candidate_turns[0].utterances[0].source_ref if candidate_turns else None
    candidate_last_ref = candidate_turns[-1].utterances[-1].source_ref if candidate_turns else None
    context = SemanticUpdateContext(
        turns=tuple(selected_turns),
        covered_through_source_ref=(
            f"{session.session_id}#{covered_uuid}" if covered_uuid is not None else None
        ),
        candidate_first_source_ref=candidate_first_ref,
        candidate_last_source_ref=candidate_last_ref,
        range_omitted_turn_count=len(omitted_turns),
        range_omitted_through_source_ref=(
            omitted_turns[-1].utterances[-1].source_ref if omitted_turns else None
        ),
    )
    logger.info(
        "semantic update context session=%s target_first=%s target_last=%s "
        "checkpoint_status=%s covered_through=%s candidate_turns=%d selected_turns=%d "
        "range_omitted_turns=%d selected_refs=%s",
        session.session_id,
        target.utterances[0].source_ref,
        target.utterances[-1].source_ref,
        resolution.status,
        context.covered_through_source_ref or "none",
        len(candidate_turns),
        len(selected_turns),
        len(omitted_turns),
        context.source_refs,
    )
    return ConversationFragment(
        session_id=target.session_id,
        utterances=target.utterances,
        persona_id=target.persona_id,
        owner_user_id=target.owner_user_id,
        semantic_update_context=context if not context.is_empty() else None,
    )


def _project_utterance(event: Event, *, session_id: str) -> Utterance | None:
    if event.type == "user_message":
        content = event.payload.get("content", "")
        if isinstance(content, str) and content.strip():
            return Utterance(
                speaker="user",
                text=content,
                ts=event.ts,
                source_ref=f"{session_id}#{event.uuid}",
            )
    elif event.type == "assistant_message":
        if event.payload.get("partial"):
            return None
        content = event.payload.get("content", "")
        if isinstance(content, str) and content.strip():
            return Utterance(
                speaker="agent",
                text=content,
                ts=event.ts,
                source_ref=f"{session_id}#{event.uuid}",
            )
    return None


def _complete_conversation_turns(
    events: list[Event],
    *,
    session_id: str,
) -> list[SemanticUpdateTurn]:
    turns: list[SemanticUpdateTurn] = []
    current: list[Utterance] = []

    for ev in events:
        if ev.type == "user_message":
            _append_complete_turn(turns, current)
            utterance = _project_utterance(ev, session_id=session_id)
            current = [utterance] if utterance is not None else []
        elif ev.type == "assistant_message":
            utterance = _project_utterance(ev, session_id=session_id)
            if current and utterance is not None:
                current.append(utterance)
    _append_complete_turn(turns, current)
    return turns


def _append_complete_turn(
    turns: list[SemanticUpdateTurn],
    utterances: list[Utterance],
) -> None:
    if utterances and any(utterance.speaker == "agent" for utterance in utterances):
        turns.append(SemanticUpdateTurn(utterances=tuple(utterances)))
