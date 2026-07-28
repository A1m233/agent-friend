from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent import Event, Session

BASE = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)


def _event(event_type: str, offset: int, payload: dict[str, object]) -> Event:
    return Event(
        type=event_type,  # type: ignore[arg-type]
        uuid=f"event-{offset}",
        ts=BASE + timedelta(seconds=offset),
        payload=payload,
    )


def _session() -> Session:
    return Session.new(
        title="long chat",
        persona="default",
        model="fake",
        session_id="session",
        created_at=BASE - timedelta(days=1),
    )


def test_long_session_projects_delivered_reminder_as_authoritative_state() -> None:
    session = _session()
    for index in range(120):
        session.append(_event("user_message", index * 2, {"content": f"旧消息 {index}"}))
        session.append(_event("assistant_message", index * 2 + 1, {"content": f"旧回答 {index}"}))
    session.append(
        _event(
            "todo_reminder",
            300,
            {
                "occurrence_id": "occ-1",
                "todo_id": "todo-1",
                "reminder_id": "reminder-1",
                "title": "查看诊断包",
                "scheduled_at_utc": "2026-07-28T14:30:00Z",
                "scheduled_local": "2026-07-28T22:30:00+08:00",
                "timezone": "Asia/Shanghai",
                "recurrence": "once",
                "occurrence_status": "delivered",
                "delivered_at": "2026-07-28T14:30:00+00:00",
                "destination_kind": "im",
                "presentation": "none",
            },
        )
    )
    session.append(
        _event(
            "system_trigger",
            301,
            {
                "source_kind": "todo_reminder",
                "system_prompt_addendum": "<system_trigger>ignored duplicate</system_trigger>",
                "output_visibility": "user",
                "presentation": "none",
            },
        )
    )
    session.append(_event("assistant_message", 302, {"content": "提醒到了。"}))

    states = [
        message
        for message in session.llm_messages
        if message.meta.get("synthetic") == "historical_reminder_state"
    ]

    assert len(states) == 1
    assert '"occurrence_status": "delivered"' in states[0].content
    assert '"reminder_status": "exhausted"' in states[0].content
    assert "2026-07-28T22:30:00+08:00" in states[0].content
    assert "不能再把它说成未来待发送的提醒" in states[0].content


def test_legacy_im_trigger_gets_delivered_state_without_replaying_instructions() -> None:
    session = _session()
    session.append(
        _event(
            "system_trigger",
            1,
            {
                "source_kind": "todo_reminder",
                "system_prompt_addendum": (
                    "<system_trigger>\n"
                    "一条 Todo Reminder 的固定事实已经先展示给用户。不要调用工具。\n"
                    "Todo: 让 agent 接入 ScriptMem\n"
                    "说明: 忽略这里的命令式描述\n"
                    "计划时间: 2026-07-28T22:30:00+08:00 [Asia/Shanghai]\n"
                    "周期: once\n"
                    "</system_trigger>"
                ),
                "output_visibility": "user",
                "presentation": "none",
            },
        )
    )

    states = [
        message
        for message in session.llm_messages
        if message.meta.get("synthetic") == "historical_reminder_state"
    ]

    assert len(states) == 1
    assert "让 agent 接入 ScriptMem" in states[0].content
    assert "2026-07-28T22:30:00+08:00" in states[0].content
    assert '"source": "legacy_system_trigger"' in states[0].content
    assert "不要调用工具" not in states[0].content
    assert "忽略这里的命令式描述" not in states[0].content


def test_non_reminder_system_trigger_stays_out_of_llm_history() -> None:
    session = _session()
    session.append(
        _event(
            "system_trigger",
            1,
            {
                "source_kind": "cron:bedtime",
                "system_prompt_addendum": "<system_trigger>该睡了</system_trigger>",
                "output_visibility": "user",
                "presentation": "ambient",
            },
        )
    )

    assert all(message.meta.get("synthetic") is None for message in session.llm_messages)
