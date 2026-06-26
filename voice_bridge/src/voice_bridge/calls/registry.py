"""``CallRegistry`` —— 通话注册表。

把"voice_bridge 知道这通通话归属哪个 session"的事承担起来——LLM 入站代理
按 ``call_id`` 反查 ``session_id`` 注入 header；控制平面查通话状态也走这。

本期纯内存（dict + RLock）：进程重启即丢，火山自身的 idle timeout（spike 实测
约几分钟）会兜底清理孤儿 RTC 任务。这是 acceptable 的退化——产品化阶段如果
接受不了再加持久化。

详见 docs/requirements/007-voice-call/design.md §4.7。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

CallState = Literal["pending", "active", "stopped", "error"]
"""通话状态机本期 3 态。``pending`` / ``ai_joined`` 等细分留给未来订阅
``EnableConversationStateCallback`` 再做（design §4.3.3）。"""


@dataclass(frozen=True)
class CallBinding:
    """一通通话的全部内存状态。

    Attributes:
        call_id: voice_bridge 自己签发的 uuid，等同火山 ``TaskId``。
        session_id: agent_bridge 持久化 session 的 id；LLM proxy 注入到
            ``X-Agent-Friend-Session-Id`` header。
        state: 当前状态机。
        started_at: 拨打时间。
        room_id: 火山 RTC 房间 id。
        bot_user_id: AI 在 RTC 房间里的 user id（火山要求必填）。
        target_user_id: 用户在 RTC 房间里的 user id；surface 拿到后用它作为
            自己的 RTC 客户端身份。
    """

    call_id: str
    session_id: str
    state: CallState
    started_at: datetime
    room_id: str
    bot_user_id: str
    target_user_id: str
    created_session: bool = False
    """本次通话是否由 voice_bridge 临时创建了新的 agent session。"""
    trace_id: str = ""
    """端到端 latency trace id；为空时调用方可回退到 call_id。"""
    round_seq: int = 0
    """同一通话内 LLM proxy inbound 的轮次计数。"""


class CallRegistry:
    """call_id → CallBinding 内存表。

    线程安全：用 :class:`threading.RLock` 保护所有读写。FastAPI 单 worker 下
    asyncio 是单线程，理论上没有竞态；显式加锁是为了未来如果切多 worker 时
    不需要重写。
    """

    def __init__(self) -> None:
        self._bindings: dict[str, CallBinding] = {}
        self._lock = threading.RLock()

    def bind(self, binding: CallBinding) -> None:
        """登记一通新通话。

        Raises:
            KeyError: ``call_id`` 已存在（重复登记，调用方 bug）。
        """
        with self._lock:
            if binding.call_id in self._bindings:
                raise KeyError(f"call_id 重复: {binding.call_id!r}")
            self._bindings[binding.call_id] = binding

    def lookup(self, call_id: str) -> CallBinding | None:
        """按 call_id 查找；不存在时返回 ``None``。"""
        with self._lock:
            return self._bindings.get(call_id)

    def update_state(self, call_id: str, state: CallState) -> CallBinding:
        """更新通话状态，返回更新后的 binding。

        Raises:
            KeyError: ``call_id`` 不存在。
        """
        with self._lock:
            current = self._bindings.get(call_id)
            if current is None:
                raise KeyError(f"call_id 不存在: {call_id!r}")
            updated = replace(current, state=state)
            self._bindings[call_id] = updated
            return updated

    def next_round(self, call_id: str) -> CallBinding:
        """Increment and return the binding for the next voice LLM round."""
        with self._lock:
            current = self._bindings.get(call_id)
            if current is None:
                raise KeyError(f"call_id 不存在: {call_id!r}")
            updated = replace(current, round_seq=current.round_seq + 1)
            self._bindings[call_id] = updated
            return updated

    def unbind(self, call_id: str) -> CallBinding | None:
        """从注册表移除一通通话；返回被移除的 binding（或 ``None``）。"""
        with self._lock:
            return self._bindings.pop(call_id, None)

    def list_active(self) -> list[CallBinding]:
        """列出所有当前 ``state="active"`` 的通话。仅调试 / 测试用。"""
        with self._lock:
            return [b for b in self._bindings.values() if b.state == "active"]

    @staticmethod
    def now() -> datetime:
        """统一的 UTC 时间源（便于测试注入）。"""
        return datetime.now(UTC)
