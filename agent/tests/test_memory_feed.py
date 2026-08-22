"""``agent.memory_feed.project_turn`` 单测：会话事件 → 记忆素材的过滤投影。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.memory_feed import project_post_turn_observation, project_turn
from agent.sessions import Event, Session


def _ev(
    type_: str, uuid: str, payload: dict[str, object], meta: dict[str, object] | None = None
) -> Event:
    return Event(type=type_, uuid=uuid, ts=datetime.now(UTC), payload=payload, meta=meta or {})  # type: ignore[arg-type]


def test_keeps_user_and_assistant_drops_noise() -> None:
    events = [
        _ev("user_message", "u1", {"content": "我养了猫"}),
        _ev("assistant_message", "a1", {"content": "真好呀", "partial": False}),
        _ev("tool_call_request", "t1", {"tool_name": "web_search", "args": {}}),
        _ev("tool_call_result", "t2", {"content": "一大段噪声"}),
        _ev("persona_change", "pc", {"to": "x"}),
        _ev("model_change", "mc", {"to": "y"}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")

    assert [u.speaker for u in frag.utterances] == ["user", "agent"]
    assert [u.text for u in frag.utterances] == ["我养了猫", "真好呀"]
    assert frag.utterances[0].source_ref == "s1#u1"
    assert frag.persona_id == "p1"


def test_drops_partial_assistant() -> None:
    events = [
        _ev("user_message", "u1", {"content": "在吗"}),
        _ev("assistant_message", "a1", {"content": "我在", "partial": True}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")
    assert [u.text for u in frag.utterances] == ["在吗"]


def test_drops_empty_content() -> None:
    events = [
        _ev("user_message", "u1", {"content": ""}),
        _ev("assistant_message", "a1", {"content": "   ", "partial": False}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")
    assert frag.is_empty()


def _session_with_turns(count: int) -> tuple[Session, list[tuple[Event, Event]]]:
    session = Session.new(title="memory feed", persona="default", model="m", session_id="s1")
    turns: list[tuple[Event, Event]] = []
    for index in range(1, count + 1):
        user = _ev("user_message", f"u{index}", {"content": f"用户 {index}"})
        assistant = _ev(
            "assistant_message",
            f"a{index}",
            {"content": f"助手 {index}", "partial": False},
        )
        session.events.extend((user, assistant))
        turns.append((user, assistant))
    return session, turns


def test_post_turn_context_uses_latest_ten_complete_prior_turns() -> None:
    session, turns = _session_with_turns(13)
    target_start_idx = session.events.index(turns[-1][0])

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert [utterance.text for utterance in fragment.utterances] == ["用户 13", "助手 13"]
    assert fragment.semantic_update_context is not None
    context = fragment.semantic_update_context
    assert len(context.turns) == 10
    assert context.turns[0].utterances[0].text == "用户 3"
    assert context.turns[-1].utterances[-1].text == "助手 12"
    assert context.range_omitted_turn_count == 2
    assert context.range_omitted_through_source_ref == "s1#a2"


def test_post_turn_context_intersects_with_compaction_raw_tail() -> None:
    session, turns = _session_with_turns(4)
    compaction = _ev(
        "compaction",
        "c1",
        {
            "summary": "前两轮摘要",
            "covered_through_uuid": turns[1][1].uuid,
            "checkpoint_version": 1,
        },
    )
    session.events.insert(session.events.index(turns[2][0]), compaction)
    target_start_idx = session.events.index(turns[3][0])

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    context = fragment.semantic_update_context
    assert context.covered_through_source_ref == "s1#a2"
    assert context.source_refs == ("s1#u3", "s1#a3")


def test_post_turn_context_uses_new_compaction_committed_inside_target_turn() -> None:
    session, turns = _session_with_turns(4)
    target_user, target_assistant = turns[-1]
    target_start_idx = session.events.index(target_user)
    compaction = _ev(
        "compaction",
        "c-current",
        {
            "summary": "前两轮摘要",
            "covered_through_uuid": turns[1][1].uuid,
            "checkpoint_version": 2,
            "summary_source_from_uuid": turns[0][0].uuid,
            "retained_from_uuid": turns[2][0].uuid,
        },
    )
    session.events.insert(session.events.index(target_assistant), compaction)

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    assert fragment.semantic_update_context.covered_through_source_ref == "s1#a2"
    assert fragment.semantic_update_context.source_refs == ("s1#u3", "s1#a3")


def test_post_turn_context_ignores_inactive_partial_tool_and_dangling_turns() -> None:
    session, turns = _session_with_turns(2)
    old_user, old_assistant = turns[-1]
    rewrite = _ev(
        "turn_rewrite",
        "rw1",
        {
            "inactive_event_uuids": [old_user.uuid, old_assistant.uuid],
            "target_user_uuid": old_user.uuid,
        },
    )
    replacement_user = _ev("user_message", "u2-new", {"content": "替代问题"})
    partial = _ev(
        "assistant_message",
        "a2-partial",
        {"content": "未完成", "partial": True},
    )
    tool = _ev("tool_call_result", "tool-1", {"content": "工具正文"})
    replacement_assistant = _ev(
        "assistant_message",
        "a2-new",
        {"content": "替代回答", "partial": False},
    )
    dangling_user = _ev("user_message", "u3", {"content": "悬空问题"})
    target_user = _ev("user_message", "u4", {"content": "目标问题"})
    target_assistant = _ev(
        "assistant_message",
        "a4",
        {"content": "目标回答", "partial": False},
    )
    session.events.extend(
        (
            rewrite,
            replacement_user,
            partial,
            tool,
            replacement_assistant,
            dangling_user,
            target_user,
            target_assistant,
        )
    )
    target_start_idx = session.events.index(target_user)

    fragment = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    assert fragment.semantic_update_context is not None
    assert fragment.semantic_update_context.source_refs == (
        "s1#u1",
        "s1#a1",
        "s1#u2-new",
        "s1#a2-new",
    )


def test_post_turn_context_is_equivalent_after_session_reload_and_frozen_after_projection() -> None:
    session, turns = _session_with_turns(3)
    target_start_idx = session.events.index(turns[-1][0])
    before = project_post_turn_observation(
        session,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )

    reloaded = Session.from_events([Event.from_jsonl(event.to_jsonl()) for event in session.events])
    after = project_post_turn_observation(
        reloaded,
        turn_start_idx=target_start_idx,
        persona_id="p1",
    )
    assert before == after

    frozen_refs = before.semantic_update_context.source_refs  # type: ignore[union-attr]
    session.events.extend(
        (
            _ev("user_message", "u4", {"content": "后来追加"}),
            _ev(
                "assistant_message",
                "a4",
                {"content": "后来回答", "partial": False},
            ),
        )
    )
    assert before.semantic_update_context is not None
    assert before.semantic_update_context.source_refs == frozen_refs
