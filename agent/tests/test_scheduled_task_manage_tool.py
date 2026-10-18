from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agent.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore
from agent.tools import ToolRegistry
from agent.tools.builtin.scheduled_task_manage import ScheduledTaskManageTool

from agent import (
    JsonlSessionStore,
    MarkdownPromptBuilder,
    NaiveContextManager,
    SessionManager,
    make_default_registry,
)
from llm_providers import (
    LLMStreamEvent,
    LLMToolCallDelta,
    LLMTurnDone,
    ModelRequest,
    ModelResponse,
    ProviderAssistantTurn,
    ProviderToolCall,
)

NOW = datetime(2026, 8, 23, 8, tzinfo=UTC)


def _create_args() -> dict[str, Any]:
    return {
        "operation": "create",
        "name": "检查",
        "instruction": "检查状态",
        "schedule": {
            "kind": "once",
            "relative_delay": {"amount": 1, "unit": "hour"},
            "timezone": "UTC",
        },
    }


def test_current_session_is_injected_by_real_conversation(tmp_path: Path) -> None:
    service = ScheduledTaskService(ScheduledTaskStore(tmp_path / "tasks.db"))
    registry = make_default_registry(
        scheduled_task_service=service, scheduled_task_model_id="fake/model"
    )

    class Model:
        context_window = 128000
        calls = 0

        def generate(self, request: ModelRequest) -> ModelResponse:
            raise AssertionError("stream expected")

        def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
            self.calls += 1
            if self.calls == 1:
                definitions = [t["function"] for t in request.tools or []]
                tool = next(t for t in definitions if t["name"] == "scheduled_task_manage")
                assert "target_current_session" in tool["parameters"]["properties"]
                assert "__agent_friend_current_session_id" not in str(tool)
                args = json.dumps({**_create_args(), "target_current_session": True})
                yield LLMToolCallDelta(
                    index=0,
                    tool_call_id="create",
                    tool_name="scheduled_task_manage",
                    args_json_delta=args,
                )
                yield LLMTurnDone(
                    stop_reason="tool_use",
                    assistant_turn=ProviderAssistantTurn(
                        content=None,
                        tool_calls=(
                            ProviderToolCall(
                                index=0,
                                tool_call_id="create",
                                tool_name="scheduled_task_manage",
                                arguments_json=args,
                            ),
                        ),
                    ),
                )
            else:
                results = [m for m in request.messages if m["role"] == "tool"]
                assert json.loads(results[-1]["content"])["status"] == "ok"
                yield LLMTurnDone(
                    stop_reason="end_turn", assistant_turn=ProviderAssistantTurn(content="已创建")
                )

    model = Model()
    manager = SessionManager(
        store=JsonlSessionStore(tmp_path / "sessions"),
        llm_client_factory=lambda _: model,
        prompt_builder_factory=lambda persona_id: MarkdownPromptBuilder(persona_id=persona_id),
        context_manager_factory=NaiveContextManager,
        tool_registry=registry,
    )
    session = manager.create(persona="default", model="deepseek/deepseek-chat")
    validated: list[str] = []
    service.target_validator = validated.append
    conv = manager.start_conversation(session)
    list(conv.stream("就在当前会话，一小时后检查状态"))
    tasks = json.loads(registry.get("scheduled_task_manage").invoke({"operation": "list"}).text)[
        "tasks"
    ]
    assert len(tasks) == 1
    assert tasks[0]["target_session_id"] == session.session_id
    assert tasks[0]["origin_session_id"] == session.session_id
    assert validated == [session.session_id]
    manager.close()


def test_create_in_explicit_current_session_and_query(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    args = {**_create_args(), "target_current_session": True}
    registry = ToolRegistry([tool])
    definition = registry.to_openai_tools()[0]["function"]
    assert "target_current_session" in definition["parameters"]["properties"]
    assert "__agent_friend_current_session_id" not in str(definition)
    missing = tool.invoke(args)
    assert missing.is_error
    assert json.loads(missing.text)["error_kind"] == "current_session_unavailable"
    result = tool.invoke({**args, "__agent_friend_current_session_id": "host-session"})
    assert not result.is_error
    task = json.loads(result.text)["task"]
    assert task["target_session_id"] == "host-session"
    assert json.loads(tool.invoke({"operation": "get", "task_id": task["id"]}).text)["task"] == task
    assert json.loads(tool.invoke({"operation": "list"}).text)["tasks"] == [task]


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"target_current_session": False},
        {"target_current_session": True, "target_session_id": "other"},
    ],
)
def test_create_rejects_ambiguous_or_missing_target(tmp_path: Path, target: dict[str, Any]) -> None:
    tool = _tool(tmp_path)
    args = {**_create_args(), **target}
    assert tool.invoke(args).is_error
    assert ToolRegistry([tool]).invoke("scheduled_task_manage", args).is_error
    assert json.loads(tool.invoke({"operation": "list"}).text)["tasks"] == []


def _tool(tmp_path: Path) -> ScheduledTaskManageTool:
    service = ScheduledTaskService(
        ScheduledTaskStore(tmp_path / "scheduled.db"),
        clock=lambda: NOW,
    )
    return ScheduledTaskManageTool(
        service,
        default_persona_id="persona-id",
        default_persona_name="default",
        default_model_id="fake/model",
    )


def test_create_list_run_and_history(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    created = tool.invoke(
        {
            "operation": "create",
            "target_session_id": "fixed-session",
            "name": "一小时后总结",
            "instruction": "总结今天的工作",
            "schedule": {
                "kind": "once",
                "relative_delay": {"amount": 1, "unit": "hour"},
                "timezone": "Asia/Shanghai",
            },
            "__agent_friend_runtime_now": NOW.isoformat(),
            "__agent_friend_current_session_id": "origin-session",
        }
    )
    assert created.is_error is False
    payload = json.loads(created.text)
    task_id = payload["task"]["id"]
    assert payload["task"]["next_fire_at_utc"] == "2026-08-23T09:00:00Z"

    listed = json.loads(tool.invoke({"operation": "list"}).text)
    assert [item["id"] for item in listed["tasks"]] == [task_id]

    updated = json.loads(
        tool.invoke(
            {
                "operation": "update",
                "task_id": task_id,
                "revision": payload["task"]["revision"],
                "name": "更新后的总结",
            }
        ).text
    )["task"]
    paused = json.loads(
        tool.invoke(
            {
                "operation": "pause",
                "task_id": task_id,
                "revision": updated["revision"],
            }
        ).text
    )["task"]
    resumed = json.loads(
        tool.invoke(
            {
                "operation": "resume",
                "task_id": task_id,
                "revision": paused["revision"],
            }
        ).text
    )["task"]
    fetched = json.loads(tool.invoke({"operation": "get", "task_id": task_id}).text)["task"]
    assert fetched == resumed

    immediate = json.loads(tool.invoke({"operation": "run_now", "task_id": task_id}).text)
    assert immediate["occurrence"]["state"] == "pending"
    history = json.loads(tool.invoke({"operation": "history", "task_id": task_id}).text)
    assert [item["id"] for item in history["occurrences"]] == [immediate["occurrence"]["id"]]
    deleted = json.loads(
        tool.invoke(
            {
                "operation": "delete",
                "task_id": task_id,
                "revision": resumed["revision"],
            }
        ).text
    )["task"]
    assert deleted["status"] == "deleted"


def test_schema_rejects_minute_fixed_interval(tmp_path: Path) -> None:
    registry = ToolRegistry(
        [
            ScheduledTaskManageTool(
                ScheduledTaskService(ScheduledTaskStore(tmp_path / "schema.db")),
                default_persona_id=None,
                default_persona_name="default",
                default_model_id="fake/model",
            )
        ]
    )
    definition = next(
        item
        for item in registry.to_openai_tools()
        if item["function"]["name"] == "scheduled_task_manage"
    )
    schedule_variants = definition["function"]["parameters"]["properties"]["schedule"]["oneOf"]
    fixed = next(
        item
        for item in schedule_variants
        if item["properties"]["kind"]["const"] == "fixed_interval"
    )
    assert fixed["properties"]["unit"]["enum"] == ["hour", "day", "week"]
    assert definition["function"]["parameters"]["additionalProperties"] is False
    update_variant = next(
        item
        for item in definition["function"]["parameters"]["oneOf"]
        if item["properties"]["operation"]["const"] == "update"
    )
    clear_variant = next(
        item for item in update_variant["anyOf"] if "clear_skill" in item.get("properties", {})
    )
    assert clear_variant["properties"]["clear_skill"] == {"const": True}
    conflicting = registry.invoke(
        "scheduled_task_manage",
        {
            "operation": "update",
            "task_id": "task-id",
            "revision": 1,
            "skill_id": "personal/example",
            "clear_skill": True,
        },
    )
    assert conflicting.is_error
    assert json.loads(conflicting.text)["error"] == "invalid_arguments"


def test_history_projects_waiting_for_approval_without_changing_run_state(tmp_path: Path) -> None:
    store = ScheduledTaskStore(tmp_path / "approval.db")
    service = ScheduledTaskService(store, clock=lambda: NOW)
    calls: list[tuple[str, str]] = []

    def approval_pending(session_id: str, run_id: str) -> bool:
        calls.append((session_id, run_id))
        return True

    tool = ScheduledTaskManageTool(
        service,
        default_persona_id=None,
        default_persona_name="default",
        default_model_id="fake/model",
        approval_pending=approval_pending,
    )
    created = json.loads(
        tool.invoke(
            {
                "operation": "create",
                "target_session_id": "fixed-session",
                "name": "审批投影",
                "instruction": "执行",
                "schedule": {
                    "kind": "once",
                    "relative_delay": {"amount": 1, "unit": "hour"},
                    "timezone": "UTC",
                },
                "__agent_friend_runtime_now": NOW.isoformat(),
            }
        ).text
    )
    task_id = created["task"]["id"]
    tool.invoke({"operation": "run_now", "task_id": task_id})
    skipped = tool.invoke({"operation": "run_now", "task_id": task_id})
    assert skipped.is_error
    assert skipped.meta["error_kind"] == "task_run_in_progress"
    claimed = store.claim_pending(NOW)[0].occurrence

    history = json.loads(tool.invoke({"operation": "history", "task_id": task_id}).text)
    assert history["occurrences"][0]["state"] == "claimed"
    assert history["occurrences"][0]["waiting_for_approval"] is True
    assert calls == [(claimed.session_id, claimed.run_id)]
