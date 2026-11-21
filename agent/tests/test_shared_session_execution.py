"""Paired checks at the two public conversation entry points."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from agent.companionship.model import GovernedModel
from agent.tools import ToolRegistry
from llm_providers.errors import LLMNetworkError

from agent import Conversation
from llm_providers import LLMStreamEvent, LLMTextDelta, LLMToolCallDelta, LLMTurnDone
from memory import MemoryContext

from .test_assistant_phase import _make_conversation
from .test_companionship_images import client
from .test_skill_conversation import _conversation
from .test_tool_calling_integration import EchoTool


def run(conversation: Conversation, autonomous: bool, **kwargs: Any) -> list[Any]:
    if autonomous:
        return list(
            conversation.dispatch_system_turn(
                source_kind="companionship",
                system_prompt_addendum="宿主唤醒，继续已有活动。",
                presentation="none",
                tool_mode="normal",
                **kwargs,
            )
        )
    return list(conversation.stream("当前任务", **kwargs))


@pytest.mark.parametrize(
    "autonomous,clue,expected", [(False, None, 1), (True, None, 0), (True, "方舟新约定", 1)]
)
def test_recall_uses_content_clue_without_turning_it_into_user_authorization(
    tmp_path: Path, autonomous: bool, clue: str | None, expected: int
) -> None:
    conv, model, service, _ = _conversation(tmp_path)
    memory = Mock()
    memory.retrieve.return_value = MemoryContext("约定的时间是五点。", [])
    conv._memory = memory
    conv._post_turn_external = True
    try:
        run(conv, autonomous, **({"recall_query": clue} if autonomous else {}))
        assert memory.retrieve.call_count == expected
        if expected:
            assert memory.retrieve.call_args.args[0] == (clue if autonomous else "当前任务")
        text = str(model.received[0])
        assert ("约定的时间是五点。" in text) is bool(expected)
        assert "<skills_catalog>" in text
        users = [e for e in conv.session.events if e.type == "user_message"]
        assert len(users) == int(not autonomous)
        assert conv._active_skill_turn_snapshot is None
        assert conv._active_tool_run_context is None
    finally:
        service.stop()


@pytest.mark.parametrize("autonomous", [False, True])
def test_failed_preparation_releases_turn_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, autonomous: bool
) -> None:
    conv, _, service, _ = _conversation(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("catalog unavailable")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, "begin_turn", fail)
            with pytest.raises(ValueError, match="catalog unavailable"):
                run(conv, autonomous, run_id="failed")
        assert conv._run_id_override is None
        assert conv._run_model_snapshot is None
        assert conv._active_skill_turn_snapshot is None
        assert conv._active_tool_run_context is None
        run(conv, autonomous, run_id="next")
    finally:
        service.stop()


@pytest.mark.parametrize("autonomous", [False, True])
@pytest.mark.parametrize("failure", ["recover", "exhaust", "cancel"])
def test_provider_retries_preserve_tool_execution_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, autonomous: bool, failure: str
) -> None:
    model = client(chat=True)
    model._network_retries = 1
    echo = EchoTool()
    conv = _make_conversation(tmp_path, fake_llm=model)  # type: ignore[arg-type]
    conv._tool_registry = ToolRegistry([echo])
    stopped = False
    calls: list[int] = []

    def check() -> None:
        if stopped:
            raise InterruptedError("cancelled")

    if autonomous:
        conv.model_guard = lambda bound: GovernedModel(
            bound,
            record_call=lambda: None,
            check=check,
            submitted=lambda: None,
            usage=lambda _: None,
        )

    def once(*args: Any, **kwargs: Any) -> Iterator[LLMStreamEvent]:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            yield LLMToolCallDelta(0, "echo-once", "echo", json.dumps({"message": "once"}))
            yield LLMTurnDone("tool_use")
            return
        if len(calls) == 2 or failure == "exhaust":
            raise LLMNetworkError("temporary")
        yield LLMTextDelta("finished")
        yield LLMTurnDone("end_turn")

    def wait(*args: Any, **kwargs: Any) -> None:
        nonlocal stopped
        stopped = failure == "cancel"
        kwargs["cancel_check"]()

    monkeypatch.setattr(model, "_stream_once", once)
    monkeypatch.setattr(model, "_wait_before_retry", wait)
    if failure == "recover":
        run(conv, autonomous, cancel_check=check)
    else:
        with pytest.raises(InterruptedError if failure == "cancel" else LLMNetworkError):
            run(conv, autonomous, cancel_check=check)
    assert len(calls) == (2 if failure == "cancel" else 3)
    assert echo.call_count == 1
    assert conv._active_tool_run_context is None
