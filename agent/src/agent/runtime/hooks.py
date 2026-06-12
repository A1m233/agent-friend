"""014 · Hook 体系：四点位（PreTurn / PostTurn / PreToolUse / PostToolUse）。

:class:`AgentRuntime` 在 dispatch 链上的固定回调点。注册顺序 = 执行顺序；
**单 hook 抛异常隔离不污染主流程**（log warning 后继续）。

短路语义：

- ``PreTurn`` hook 返回 :data:`PRE_TURN_SKIP` 让 main loop 跳过本轮 dispatch
  （用于"专注模式静默"等场景；agent 选择不开口）
- ``PreToolUse`` hook 返回 :func:`pre_tool_block` 阻止 tool 执行
  （为 Tier 2 Permission 留口子）
- ``PostTurn`` / ``PostToolUse`` 不短路（结果已产生），仅作旁路观察。

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §5。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sessions import Session
    from ..tools import ToolResult
    from .inbox import AgentEvent


logger = logging.getLogger(__name__)


class HookKind(StrEnum):
    """Hook 点位枚举。"""

    PRE_TURN = "pre_turn"
    POST_TURN = "post_turn"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"


@dataclass(frozen=True)
class PreTurnDecision:
    """PreTurn hook 返回值。

    使用模块级常量 :data:`PRE_TURN_PROCEED` / :data:`PRE_TURN_SKIP`；
    用 ``is`` 比较即可（frozen dataclass 单例特性 + Python identity 比较）。
    """

    skip: bool
    reason: str = ""


PRE_TURN_PROCEED: PreTurnDecision = PreTurnDecision(skip=False)
"""默认放行——main loop 进入本轮 dispatch。"""

PRE_TURN_SKIP: PreTurnDecision = PreTurnDecision(skip=True, reason="default skip sentinel")
"""跳过本轮 dispatch——main loop 选择沉默（不开口、不进 inner loop）。"""


@dataclass(frozen=True)
class PreToolUseDecision:
    """PreToolUse hook 返回值。

    ``block=False`` → tool 正常执行；``block=True`` → 不调真 tool，
    把 ``blocked_result_text`` 作为 :class:`ToolResult` ``is_error=True`` 喂回 LLM。
    用 :func:`pre_tool_block` 构造 BLOCK 形态、:data:`PRE_TOOL_PROCEED` 取放行单例。
    """

    block: bool
    blocked_result_text: str = ""


PRE_TOOL_PROCEED: PreToolUseDecision = PreToolUseDecision(block=False)
"""默认放行——执行真 tool。"""


def pre_tool_block(reason: str) -> PreToolUseDecision:
    """构造 BLOCK 形态决策；``reason`` 作为喂回 LLM 的 tool_result 文本。"""
    return PreToolUseDecision(block=True, blocked_result_text=reason)


@dataclass(frozen=True)
class PostTurnContext:
    """PostTurn hook 的输入上下文。

    Attributes:
        session: 本轮跑完后的 :class:`agent.Session` 实例。
        turn_start_idx: 本轮新增 events 的切片起点（``session.events[turn_start_idx:]``
            可拿到本轮新落的全部事件）。
        event: 触发本轮的 :class:`AgentEvent`，hook 可据此分支
            （如 silent turn 跳过 memory 旁路）。
    """

    session: Session
    turn_start_idx: int
    event: AgentEvent


PreTurnHook = Callable[["AgentEvent"], PreTurnDecision]
PostTurnHook = Callable[[PostTurnContext], None]
PreToolUseHook = Callable[[str, dict[str, Any]], PreToolUseDecision]
PostToolUseHook = Callable[[str, dict[str, Any], "ToolResult"], None]


class HookRegistry:
    """四点位 hook 的注册 + 执行 + 错误隔离。

    每个点位是独立的 callback 链；注册顺序 = 执行顺序；单 callback 抛异常
    隔离（log warning 后继续下一个），保证主 dispatch 流不被污染。

    Pre-\\* 短路语义：

    - ``run_pre_turn``：任一 callback 返回 ``skip=True``，立即返回该 decision
      （后续 PreTurn hook **不再调**——SKIP 是终态）
    - ``run_pre_tool_use``：任一 callback 返回 ``block=True``，立即返回该 decision
      （后续 PreToolUse hook **不再调**——BLOCK 是终态）

    抛异常视为"未表态"——按 PROCEED 处理，继续下一个 callback。
    """

    def __init__(self) -> None:
        self._pre_turn: list[PreTurnHook] = []
        self._post_turn: list[PostTurnHook] = []
        self._pre_tool_use: list[PreToolUseHook] = []
        self._post_tool_use: list[PostToolUseHook] = []

    def register(
        self,
        kind: HookKind,
        callback: Callable[..., Any],
    ) -> None:
        """注册一个 callback 到对应点位（追加到链尾，注册顺序 = 执行顺序）。"""
        if kind is HookKind.PRE_TURN:
            self._pre_turn.append(callback)
        elif kind is HookKind.POST_TURN:
            self._post_turn.append(callback)
        elif kind is HookKind.PRE_TOOL_USE:
            self._pre_tool_use.append(callback)
        elif kind is HookKind.POST_TOOL_USE:
            self._post_tool_use.append(callback)

    def run_pre_turn(self, event: AgentEvent) -> PreTurnDecision:
        for cb in self._pre_turn:
            try:
                decision = cb(event)
            except Exception:
                logger.warning("pre_turn hook 抛异常，视作 PROCEED", exc_info=True)
                continue
            if decision.skip:
                return decision
        return PRE_TURN_PROCEED

    def run_post_turn(self, ctx: PostTurnContext) -> None:
        for cb in self._post_turn:
            try:
                cb(ctx)
            except Exception:
                logger.warning("post_turn hook 抛异常", exc_info=True)

    def run_pre_tool_use(
        self,
        name: str,
        args: dict[str, Any],
    ) -> PreToolUseDecision:
        for cb in self._pre_tool_use:
            try:
                decision = cb(name, args)
            except Exception:
                logger.warning("pre_tool_use hook 抛异常，视作 PROCEED", exc_info=True)
                continue
            if decision.block:
                return decision
        return PRE_TOOL_PROCEED

    def run_post_tool_use(
        self,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        for cb in self._post_tool_use:
            try:
                cb(name, args, result)
            except Exception:
                logger.warning("post_tool_use hook 抛异常", exc_info=True)
