"""014 单测：``agent.runtime.hooks.HookRegistry`` 注册 / 执行 / 短路 / 错误隔离。

覆盖 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §5；
对应 requirement.md AC-2（四点位可注册可触发 + 错误隔离）+ AC-3（Pre-\\* 短路）。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agent.runtime import (
    PRE_TOOL_PROCEED,
    PRE_TURN_PROCEED,
    PRE_TURN_SKIP,
    HookKind,
    HookRegistry,
    PostTurnContext,
    PreToolUseDecision,
    PreTurnDecision,
    SystemTriggerEvent,
    UserEvent,
    pre_tool_block,
)
from agent.runtime.inbox import AgentEvent
from agent.tools import ToolResult

# ----- 工厂助手：注册时绑定 log 列表 + label + 返回 decision -----


def _pre_turn_logger(
    log: list[str],
    label: str,
    decision: PreTurnDecision = PRE_TURN_PROCEED,
) -> Any:
    def hook(_ev: AgentEvent) -> PreTurnDecision:
        log.append(label)
        return decision

    return hook


def _pre_tool_logger(
    log: list[str],
    label: str,
    decision: PreToolUseDecision = PRE_TOOL_PROCEED,
) -> Any:
    def hook(_name: str, _args: dict[str, Any]) -> PreToolUseDecision:
        log.append(label)
        return decision

    return hook


# ===== 注册顺序 = 执行顺序 =====


def test_pre_turn_hooks_run_in_registration_order() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "a"))
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "b"))
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "c"))

    decision = reg.run_pre_turn(UserEvent(session_id="s", user_input="x"))
    assert decision is PRE_TURN_PROCEED
    assert log == ["a", "b", "c"]


def test_post_turn_hooks_run_in_registration_order() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.POST_TURN, lambda _ctx: log.append("a"))
    reg.register(HookKind.POST_TURN, lambda _ctx: log.append("b"))

    session = MagicMock()
    reg.run_post_turn(
        PostTurnContext(
            session=session,
            turn_start_idx=0,
            event=UserEvent(session_id="s", user_input="x"),
        )
    )
    assert log == ["a", "b"]


# ===== PreTurn SKIP 短路 =====


def test_pre_turn_skip_short_circuits() -> None:
    """任一 PreTurn hook 返回 SKIP 立即终态——后续 hook 不再调。"""
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "a"))
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "b", PRE_TURN_SKIP))
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "c-should-not-run"))

    decision = reg.run_pre_turn(UserEvent(session_id="s", user_input="x"))
    assert decision.skip is True
    assert log == ["a", "b"]


def test_pre_turn_proceed_when_no_hooks() -> None:
    reg = HookRegistry()
    decision = reg.run_pre_turn(UserEvent(session_id="s", user_input="x"))
    assert decision is PRE_TURN_PROCEED


# ===== PreToolUse BLOCK 短路 =====


def test_pre_tool_use_block_short_circuits() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.PRE_TOOL_USE, _pre_tool_logger(log, "a"))
    reg.register(
        HookKind.PRE_TOOL_USE,
        _pre_tool_logger(log, "b", pre_tool_block("denied by policy")),
    )
    reg.register(HookKind.PRE_TOOL_USE, _pre_tool_logger(log, "c-should-not-run"))

    decision = reg.run_pre_tool_use("echo", {"message": "hi"})
    assert decision.block is True
    assert decision.blocked_result_text == "denied by policy"
    assert log == ["a", "b"]


def test_pre_tool_use_proceed_when_no_hooks() -> None:
    reg = HookRegistry()
    decision = reg.run_pre_tool_use("echo", {})
    assert decision is PRE_TOOL_PROCEED


# ===== Post-\* 不短路（即使有"想短路"的语义也无效） =====


def test_post_tool_use_all_hooks_run_regardless() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.POST_TOOL_USE, lambda _n, _a, _r: log.append("a"))
    reg.register(HookKind.POST_TOOL_USE, lambda _n, _a, _r: log.append("b"))
    reg.register(HookKind.POST_TOOL_USE, lambda _n, _a, _r: log.append("c"))

    reg.run_post_tool_use("echo", {}, ToolResult(text="ok"))
    assert log == ["a", "b", "c"]


# ===== 错误隔离（AC-2 核心） =====


def test_single_pre_turn_hook_exception_does_not_break_others() -> None:
    """单 hook 抛异常隔离——其他 hook 仍调用、整体决策默认 PROCEED。"""
    reg = HookRegistry()
    log: list[str] = []

    def raising_hook(_ev: AgentEvent) -> PreTurnDecision:
        log.append("raising")
        raise RuntimeError("boom")

    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "a"))
    reg.register(HookKind.PRE_TURN, raising_hook)
    reg.register(HookKind.PRE_TURN, _pre_turn_logger(log, "c"))

    decision = reg.run_pre_turn(UserEvent(session_id="s", user_input="x"))
    # 抛异常视为 PROCEED，后续 hook 仍跑
    assert decision is PRE_TURN_PROCEED
    assert log == ["a", "raising", "c"]


def test_single_post_turn_hook_exception_does_not_break_others() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.POST_TURN, lambda _ctx: log.append("a"))

    def raising_hook(_ctx: Any) -> None:
        log.append("raising")
        raise RuntimeError("boom")

    reg.register(HookKind.POST_TURN, raising_hook)
    reg.register(HookKind.POST_TURN, lambda _ctx: log.append("c"))

    session = MagicMock()
    reg.run_post_turn(
        PostTurnContext(
            session=session,
            turn_start_idx=0,
            event=UserEvent(session_id="s", user_input="x"),
        )
    )
    assert log == ["a", "raising", "c"]


def test_single_pre_tool_use_hook_exception_treated_as_proceed() -> None:
    reg = HookRegistry()
    log: list[str] = []

    def raising_hook(_name: str, _args: dict[str, Any]) -> PreToolUseDecision:
        log.append("raising")
        raise RuntimeError("boom")

    reg.register(HookKind.PRE_TOOL_USE, raising_hook)
    reg.register(HookKind.PRE_TOOL_USE, _pre_tool_logger(log, "after"))

    decision = reg.run_pre_tool_use("echo", {})
    assert decision is PRE_TOOL_PROCEED
    assert log == ["raising", "after"]


def test_single_post_tool_use_hook_exception_does_not_break_others() -> None:
    reg = HookRegistry()
    log: list[str] = []
    reg.register(HookKind.POST_TOOL_USE, lambda _n, _a, _r: log.append("a"))

    def raising_hook(_name: str, _args: dict[str, Any], _result: ToolResult) -> None:
        log.append("raising")
        raise RuntimeError("boom")

    reg.register(HookKind.POST_TOOL_USE, raising_hook)
    reg.register(HookKind.POST_TOOL_USE, lambda _n, _a, _r: log.append("c"))

    reg.run_post_tool_use("echo", {}, ToolResult(text="ok"))
    assert log == ["a", "raising", "c"]


# ===== Hook 能看到事件类型 =====


def test_pre_turn_hook_can_inspect_event_type() -> None:
    """PreTurn hook 能据事件类型做差异化决策（如 idle source 看到 user 时让步）。"""
    reg = HookRegistry()
    seen: list[str] = []

    def inspecting_hook(ev: AgentEvent) -> PreTurnDecision:
        if isinstance(ev, UserEvent):
            seen.append("user")
            return PRE_TURN_PROCEED
        if isinstance(ev, SystemTriggerEvent):
            seen.append(f"sys:{ev.source_kind}")
            return PRE_TURN_PROCEED
        return PRE_TURN_PROCEED

    reg.register(HookKind.PRE_TURN, inspecting_hook)
    reg.run_pre_turn(UserEvent(session_id="s", user_input="x"))
    reg.run_pre_turn(
        SystemTriggerEvent(
            session_id="s",
            source_kind="cron:bedtime",
            system_prompt_addendum="x",
        )
    )
    assert seen == ["user", "sys:cron:bedtime"]
