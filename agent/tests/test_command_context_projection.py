import json
from dataclasses import replace
from pathlib import Path

import pytest
from agent.context.command_projection import project_successful_command
from agent.messages import Message

from .test_context_summarizing import _make_conversation
from .test_context_usage_anchor import MeasuredLLM


def successful() -> Message:
    return Message(
        role="tool",
        meta={"tool_name": "exec_command", "is_error": False},
        content=json.dumps(
            {
                "mode": "buffered",
                "status": "exited",
                "process_status": "exited",
                "exit_code": 0,
                "process_id": "proc_one",
                "dropped_bytes": 0,
                "stdout": '{"status":"business_failure","observation_id":"abc"}',
                "stderr": "warning remains visible",
                "semantic_status": "not_evaluated",
                "output_seq": 1,
                "recovery_mode": "resident_only",
                "launch_seconds": 3,
                "approval": {"decision_source": "session_auto"},
                "execution_environment": {"runtime_version": "3.12"},
            }
        ),
    )


def test_projection_preserves_output_recovery_and_raw_history() -> None:
    message = successful()
    raw = message.content
    projected = project_successful_command(message)
    data = json.loads(projected.content)
    assert message.content == raw
    for name in (
        "stdout",
        "stderr",
        "process_id",
        "exit_code",
        "semantic_status",
        "output_seq",
        "recovery_mode",
        "dropped_bytes",
    ):
        assert data[name] == json.loads(raw)[name]
    assert data["result_ref"] == message.uuid
    assert "approval" not in data and "launch_seconds" not in data
    assert project_successful_command(projected) == projected


def test_image_eviction_notice_survives_success_projection() -> None:
    message = successful()
    notice = "\n[工具图像已从模型上下文移除；result_ref=ref；当前不可见]"
    message = replace(
        message,
        content=message.content + notice,
        meta={**message.meta, "context_projection": {"stage": "image_budget"}},
    )
    projected = project_successful_command(message)
    assert projected.content.endswith(notice)
    payload, _ = json.JSONDecoder().raw_decode(projected.content)
    assert "approval" not in payload
    assert payload["stdout"] == json.loads(successful().content)["stdout"]
    assert project_successful_command(projected) == projected


@pytest.mark.parametrize("recovery", [False, True])
def test_conversation_projects_only_with_recovery_available(tmp_path: Path, recovery: bool) -> None:
    from agent.context import SummarizingContextManager

    conv, _, _ = _make_conversation(
        tmp_path,
        fake_llm=MeasuredLLM(),
        context_manager=SummarizingContextManager(),
        with_recovery_tool=recovery,
    )
    message = successful()
    original = message.content
    rendered = conv._render_context_message(message)
    assert ("approval" in json.loads(rendered["content"])) != recovery
    assert message.content == original


@pytest.mark.parametrize(
    "change",
    [
        {"exit_code": 1},
        {"exit_code": False},
        {"error": "command_images_failed"},
        {"sandbox_denied": True},
        {"status": "running"},
        {"mode": "streaming"},
        {"dropped_bytes": 12},
    ],
)
def test_failure_or_incomplete_command_keeps_all_diagnostics(change: dict[str, object]) -> None:
    message = successful()
    message = replace(message, content=json.dumps({**json.loads(message.content), **change}))
    assert project_successful_command(message) is message
    error = replace(successful(), meta={"tool_name": "exec_command", "is_error": True})
    assert project_successful_command(error) is error
