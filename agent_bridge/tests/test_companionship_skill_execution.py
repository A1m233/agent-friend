"""Offline wake-to-command regression, with only process transport and approval scripted."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from agent.command_runtime import CommandApprovalDecision, CommandRuntime, ExecCommandTool
from agent.companionship.snapshot import capture_changes
from agent.scheduling import OnceSchedule
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from test_companionship_runtime import CompanionModel, isolated_runtime

from agent import Event
from llm_providers import LLMStreamEvent, LLMTextDelta, LLMToolCallDelta, LLMTurnDone, ModelRequest


class SkillModel(CompanionModel):
    skill_id = ""
    allow = True

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        self.requests.append(request)
        step = len(self.requests)
        if step == 1:
            assert self.skill_id in str(request.messages)
            name, args = (
                "read_conversation",
                {"session_id": self.target, "mode": "latest", "limit": 2},
            )
        elif step == 2:
            assert "五点左右自己跑" in str(request.messages)
            name, args = "skill_read", {"skill_id": self.skill_id, "reason": "继续约定的活动"}
        elif step == 3:
            assert "<skill_bindings" in str(request.messages)
            assert "scripts/run.py" in str(request.messages)
            name, args = (
                "exec_command",
                {
                    "argv": [sys.executable, "scripts/run.py", "fake:buffered"],
                    "skill": {"entry": "scripts/run.py"},
                    "reason": "执行本轮已加载的隔离测试脚本",
                },
            )
        else:
            result = next(m for m in reversed(request.messages) if m.get("role") == "tool")
            assert ("approval_denied" in str(result)) is (not self.allow)
            if self.allow:
                assert "buffered output" in str(result)
            yield LLMTextDelta("脚本已运行。" if self.allow else "审批拒绝，脚本未运行。")
            yield LLMTurnDone("end_turn")
            return
        yield LLMToolCallDelta(0, f"call-{step}", name, json.dumps(args))
        yield LLMTurnDone("tool_use")


@pytest.mark.parametrize(
    "autonomous,skill_name",
    [(True, "maa-daily"), (True, "test-workflow"), (False, "test-workflow")],
)
@pytest.mark.parametrize("allow", [False, True])
def test_skill_script_binding_through_chat_and_due_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, autonomous: bool, skill_name: str, allow: bool
) -> None:
    model = SkillModel()
    model.allow = allow
    runtime = isolated_runtime(tmp_path, monkeypatch, model)
    service = runtime.companionship
    scheduler = runtime.scheduled_task_service
    assert service and runtime.agent_runtime and runtime.skill_service and scheduler
    manager = runtime.persistent_session_manager
    commands = CommandRuntime(
        None,
        codex_home=tmp_path / "fake-codex",
        execution_root=tmp_path / "execution",
        exec_server_command=[
            sys.executable,
            str(Path(__file__).parents[2] / "agent/tests/fixtures/fake_exec_server.py"),
        ],
    )
    tool = runtime.tool_registry.get("exec_command")
    assert isinstance(tool, ExecCommandTool)
    monkeypatch.setattr(tool, "_runtime", commands)
    monkeypatch.setattr(tool, "_execution_environment_manager", None)
    monkeypatch.setattr(tool, "_native_tools", None)
    monkeypatch.setattr(tool, "_base_env", {})
    approvals: list[Any] = []
    starts: list[tuple[str, ...]] = []
    original_start = commands.start_process

    def approve(request: Any, **kwargs: Any) -> CommandApprovalDecision:
        approvals.append(request)
        return CommandApprovalDecision(allow=allow)

    def start(**kwargs: Any) -> Any:
        entry = Path(kwargs["argv"][1])
        assert entry.read_text(encoding="utf-8") == "print('fixture')\n"
        executed = subprocess.run(
            [sys.executable, str(entry)], cwd=kwargs["workdir"], capture_output=True, check=True
        )
        assert executed.stdout.strip() == b"fixture"
        starts.append(kwargs["argv"])
        return original_start(**kwargs)

    assert runtime.approval_broker
    monkeypatch.setattr(runtime.approval_broker, "request", approve)
    monkeypatch.setattr(commands, "start_process", start)
    try:
        package = runtime.skill_service.personal_root / skill_name
        (package / "scripts").mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Run the fixture\n---\nUse scripts/run.py.\n",
            encoding="utf-8",
        )
        (package / "scripts/run.py").write_text("print('fixture')\n", encoding="utf-8")
        catalog = runtime.skill_service.refresh()
        model.skill_id = next(item.id for item in catalog.entries if item.name == skill_name)
        target = manager.create(runtime.default_persona, runtime.default_model, title="方舟")
        model.target = target.session_id
        now = runtime.time_provider.now()
        manager._store.append_event(
            target.session_id,
            Event(
                type="user_message",
                uuid="agreement",
                ts=now,
                payload={"content": "今天跳过定时任务，五点左右自己跑"},
            ),
        )
        scheduled = scheduler.create(
            name="original daily",
            instruction="跑一下今天日常",
            schedule=OnceSchedule(
                local_datetime=(now + timedelta(days=1)).replace(tzinfo=None),
                timezone="Asia/Shanghai",
            ),
            persona_name=runtime.default_persona,
            model_id=runtime.default_model,
            target_session_id=target.session_id,
        )
        if autonomous:
            state = service.control("enable", user_initiated=True)
            inner = state["inner_session_id"]
            manager._store.append_event(
                inner,
                Event(
                    type="assistant_message",
                    uuid="remembered",
                    ts=now,
                    payload={"content": "下午五点左右再看看方舟日常。"},
                ),
            )
            snapshot = capture_changes(service.access, service.store.read(), "earlier")
            service.store.update(
                lambda s: s.update(
                    baselines=snapshot["baselines"],
                    last_overview_at=now.isoformat(),
                    next_wake_at=None,
                )
            )
            assert capture_changes(service.access, service.store.read(), "due")["entries"] == []
            runtime.agent_runtime.start()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = service.store.read()
                if state["last_outcome"] and not state["active_run_id"]:
                    break
                time.sleep(0.02)
            assert state["last_outcome"] == "completed", state
            events = manager.open(inner).events
            assert not any(e.type == "user_message" for e in events)
        else:
            conv = manager.start_conversation(
                manager.open(target.session_id), post_turn_external=True
            )
            list(conv.stream("继续执行约定。", surface=DESKTOP_CHAT_SURFACE, run_id="ordinary"))
            events = conv.session.events
        assert len(model.requests) == 4
        assert len(approvals) == 1
        assert approvals[0].details.skill.skill_id == model.skill_id
        assert len(starts) == int(allow)
        assert scheduler.get(scheduled.id) == scheduled
        result = next(e for e in reversed(events) if e.type == "tool_call_result")
        assert result.payload["is_error"] is (not allow)
    finally:
        runtime.agent_runtime.stop()
        runtime.close()
        commands.close()
