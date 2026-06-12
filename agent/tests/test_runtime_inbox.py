"""014 单测：``agent.runtime.inbox`` 事件类型。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3.1
的 AgentEvent discriminated union 字段形态与 ``queue.Queue`` 流转。
"""

from __future__ import annotations

import queue
from dataclasses import asdict

from agent.runtime import SystemTriggerEvent, UserEvent
from agent.runtime.inbox import AgentEvent


def test_user_event_fields() -> None:
    ev = UserEvent(session_id="s-1", user_input="hello")
    assert ev.session_id == "s-1"
    assert ev.user_input == "hello"
    assert ev.type == "user"


def test_system_trigger_event_defaults_visibility_user() -> None:
    ev = SystemTriggerEvent(
        session_id="s-1",
        source_kind="cron:bedtime",
        system_prompt_addendum="该睡了",
    )
    assert ev.output_visibility == "user"
    assert ev.event_metadata == {}
    assert ev.type == "system_trigger"


def test_system_trigger_event_memory_only() -> None:
    ev = SystemTriggerEvent(
        session_id="s-1",
        source_kind="idle_reflection",
        system_prompt_addendum="reflect",
        output_visibility="memory_only",
        event_metadata={"idle_minutes": 30},
    )
    assert ev.output_visibility == "memory_only"
    assert ev.event_metadata == {"idle_minutes": 30}


def test_agent_event_frozen() -> None:
    ev = UserEvent(session_id="s-1", user_input="hi")
    try:
        ev.user_input = "tampered"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("frozen dataclass should reject attribute mutation")


def test_inbox_round_trip() -> None:
    """放进 queue 取出后字段完全一致——序列化 hash 一致。"""
    inbox: queue.Queue[AgentEvent] = queue.Queue()
    a = UserEvent(session_id="s-1", user_input="a")
    b = SystemTriggerEvent(
        session_id="s-1",
        source_kind="cron:bedtime",
        system_prompt_addendum="x",
    )
    inbox.put(a)
    inbox.put(b)
    got_a = inbox.get()
    got_b = inbox.get()
    assert got_a == a
    assert got_b == b
    assert asdict(got_a) == asdict(a)
    assert asdict(got_b) == asdict(b)
