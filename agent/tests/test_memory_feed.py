"""``agent.memory_feed.project_turn`` 单测：会话事件 → 记忆素材的过滤投影。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.memory_feed import project_turn
from agent.sessions import Event


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
        _ev("assistant_message", "a1", {"content": "", "partial": False}),
    ]
    frag = project_turn(events, session_id="s1", persona_id="p1")
    assert frag.is_empty()
