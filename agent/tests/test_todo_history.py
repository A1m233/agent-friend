from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agent.todos import OnceSchedule, TodoError, TodoListQuery, TodoService, TodoStore
from agent.todos.history import TodoActor
from agent.tools.builtin.todo_manage import TodoManageTool

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)
LOCAL_NOW = NOW.replace(tzinfo=None)


def service(tmp_path: Path) -> TodoService:
    return TodoService(TodoStore(tmp_path / "todos.db"), clock=lambda: NOW)


def test_interleaved_sources_and_conflicts_survive_reopen(tmp_path: Path) -> None:
    svc = service(tmp_path)
    created = svc.create(title="写周报", actor=TodoActor("agent", "session-a"))
    edited = svc.update(
        created.todo.id, revision=1, description="人工补充", actor=TodoActor("user")
    )
    with pytest.raises(TodoError, match="其他操作"):
        svc.cancel(created.todo.id, revision=1, actor=TodoActor("agent", "session-b"))
    done = svc.complete(
        created.todo.id, revision=edited.todo.revision, actor=TodoActor("agent", "session-b")
    )
    history = TodoStore(svc.store.path).history(created.todo.id)
    assert [item["actor"] for item in history] == ["agent", "user", "agent"]
    assert history[0]["session_id"] == "session-b"
    assert history[1]["before"] == {"description": ""}
    assert history[1]["after"] == {"description": "人工补充"}
    assert history[0]["before"]["status"] == "open"
    assert history[0]["after"]["status"] == "completed"
    assert done.last_operation is not None and done.last_operation["id"] == history[0]["id"]
    svc.reopen(created.todo.id, revision=done.todo.revision, actor=TodoActor("user"))
    assert svc.store.history(created.todo.id)[1:] == history


def test_initial_reminders_are_atomic_and_invalid_create_leaves_nothing(tmp_path: Path) -> None:
    svc = service(tmp_path)
    future = OnceSchedule(LOCAL_NOW + timedelta(days=1), "UTC")
    past = OnceSchedule(LOCAL_NOW - timedelta(days=1), "UTC")
    with pytest.raises(TodoError):
        svc.create(title="无残留", reminders=(future, past), actor=TodoActor("user"))
    assert svc.list() == []
    created = svc.create(title="原子创建", reminders=(future, future), actor=TodoActor("user"))
    assert len(created.reminders) == 2
    history = svc.store.history(created.todo.id)
    assert len(history) == 1
    assert len(history[0]["after"]["reminders"]) == 2


def test_history_failure_rolls_back_business_write(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(title="原内容").todo
    with sqlite3.connect(svc.store.path) as conn:
        conn.execute("""CREATE TRIGGER reject_history BEFORE INSERT ON todo_history
            BEGIN SELECT RAISE(ABORT, 'history unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="history unavailable"):
        svc.update(todo.id, revision=1, title="未保存", actor=TodoActor("user"))
    assert svc.get(todo.id).todo.title == "原内容"
    assert svc.get(todo.id).todo.revision == 1
    assert len(svc.store.history(todo.id)) == 1


def test_reminder_changes_and_status_side_effect_have_same_actor(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(title="提醒").todo
    reminder = svc.add_reminder(
        todo.id,
        schedule=OnceSchedule(LOCAL_NOW + timedelta(days=1), "UTC"),
        actor=TodoActor("user"),
    )
    svc.update_reminder(
        todo.id,
        reminder.id,
        revision=reminder.revision,
        schedule=OnceSchedule(LOCAL_NOW + timedelta(days=2), "UTC"),
        actor=TodoActor("agent", "s"),
    )
    done = svc.complete(todo.id, revision=todo.revision, actor=TodoActor("user"))
    history = svc.store.history(todo.id)
    assert history[0]["actor"] == "user"
    assert history[0]["after"]["reminders"][reminder.id]["status"] == "disabled"
    assert len(history) == 4
    reopened = svc.reopen(todo.id, revision=done.todo.revision, actor=TodoActor("user"))
    assert reopened.reminders[0].status.value == "disabled"
    with pytest.raises(TodoError, match="其他操作"):
        svc.update_reminder(todo.id, reminder.id, revision=1, schedule=reminder.schedule)


def test_cannot_enable_reminder_on_closed_todo(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(title="关闭", reminders=(OnceSchedule(LOCAL_NOW + timedelta(days=1), "UTC"),))
    done = svc.cancel(todo.todo.id, revision=1)
    reminder = done.reminders[0]
    with pytest.raises(TodoError, match="重新打开"):
        svc.update_reminder(
            todo.todo.id, reminder.id, revision=reminder.revision, schedule=reminder.schedule
        )


def test_due_exhaustion_is_system_and_scans_do_not_duplicate(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(
        title="到期", reminders=(OnceSchedule(LOCAL_NOW + timedelta(minutes=1), "UTC"),)
    )
    svc.store.create_due_occurrences(NOW + timedelta(minutes=2))
    svc.store.create_due_occurrences(NOW + timedelta(minutes=3))
    history = svc.store.history(todo.todo.id)
    assert len(history) == 2
    assert history[0]["actor"] == "system"
    assert history[0]["action"] == "reminder_exhausted"
    assert svc.get(todo.todo.id).todo.status.value == "open"


def test_history_cursor_and_literal_search(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(title="100% Todo").todo
    svc.create(title="其它", description="Todo in description")
    assert len(svc.list(TodoListQuery(search="todo"))) == 2
    assert len(svc.list(TodoListQuery(search="%"))) == 1
    for revision in range(1, 4):
        svc.update(todo.id, revision=revision, title=f"编辑 {revision}")
    first = svc.store.history(todo.id, limit=2)
    svc.update(todo.id, revision=4, title="翻页期间新操作")
    second = svc.store.history(todo.id, before_id=first[-1]["id"], limit=2)
    assert len(first + second) == 4
    assert len({item["id"] for item in first + second}) == 4


def test_old_rows_are_unknown_without_fabricated_history(tmp_path: Path) -> None:
    svc = service(tmp_path)
    todo = svc.create(title="旧数据", origin_kind="session", origin_id="old-session").todo
    with sqlite3.connect(svc.store.path) as conn:
        conn.execute("DROP TABLE todo_history")
    reopened = service(tmp_path)
    assert reopened.get(todo.id).last_operation is None
    assert reopened.store.history(todo.id) == []
    changed = reopened.update(todo.id, revision=1, description="现在修改")
    assert changed.last_operation is not None and changed.last_operation["actor"] == "unknown"


def test_noop_has_no_history_and_agent_queries_receive_attribution(tmp_path: Path) -> None:
    import json

    svc = service(tmp_path)
    tool = TodoManageTool(svc)
    result = json.loads(
        tool.invoke(
            {
                "operation": "create",
                "title": "工具创建",
                "__agent_friend_current_session_id": "session",
            }
        ).text
    )
    todo = svc.get(result["todo"]["id"])
    assert todo.last_operation is not None
    assert todo.last_operation["actor"] == "agent"
    svc.update(
        todo.todo.id, revision=todo.todo.revision, title=todo.todo.title, actor=TodoActor("user")
    )
    assert len(svc.store.history(todo.todo.id)) == 1
    updated = svc.update(
        todo.todo.id,
        revision=svc.get(todo.todo.id).todo.revision,
        description="人工变更",
        actor=TodoActor("user"),
    )
    queried = json.loads(tool.invoke({"operation": "get", "todo_id": todo.todo.id}).text)
    assert queried["todo"]["last_operation"]["actor"] == "user"
    assert queried["todo"]["revision"] == updated.todo.revision
