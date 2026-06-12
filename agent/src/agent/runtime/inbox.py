"""014 · main loop 事件类型（AgentRuntime inbox 流转的 discriminated union）。

`UserEvent` 由 :class:`UserSource` 包装现有 user 输入路径产生；
`SystemTriggerEvent` 由 :class:`CronSource` / :class:`IdleReflectionSource`
等 source 产生。

形态承诺：**只增不减**——新增子类型不算破坏；删除 / 重命名既有子类型才算破坏。
（与 :data:`agent.ConversationEvent` 一致的协议级前向兼容约定。）

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §3.1。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class UserEvent:
    """user 输入触发轮（包装现有 :meth:`Conversation.stream` 路径）。

    Attributes:
        session_id: 路由到哪个 session 的 Conversation。
        user_input: 用户输入文本，原样作为 :meth:`Conversation.stream` 入参。
    """

    session_id: str
    user_input: str
    type: Literal["user"] = "user"


@dataclass(frozen=True)
class SystemTriggerEvent:
    """系统级触发轮（cron / idle / 后台 tool 回包等）。

    Attributes:
        session_id: 路由到哪个 session。
        source_kind: 触发源 kind，落入 session 的 ``system_trigger.payload.source_kind``
            （如 ``"cron:bedtime"`` / ``"idle_reflection"``）。
        system_prompt_addendum: 追加到 system message 末尾的引导话——经
            :meth:`Conversation.dispatch_system_turn` 透传到 LLM 的 trailing_system。
        output_visibility: ``"user"`` = 与 :meth:`Conversation.stream` 同形 yield 事件
            可被 push subscriber 看到；``"memory_only"`` = silent turn，输出仅入
            memory，不冒泡到 bridge stream（避免污染 session.messages 派生）。
        event_metadata: 旁路元数据（如 idle 触发时的累计 idle 分钟），供
            observability / 调试，**不参与 dispatch 行为决策**。
    """

    session_id: str
    source_kind: str
    system_prompt_addendum: str
    output_visibility: Literal["user", "memory_only"] = "user"
    event_metadata: dict[str, Any] = field(default_factory=dict)
    type: Literal["system_trigger"] = "system_trigger"


AgentEvent = UserEvent | SystemTriggerEvent
"""inbox 中流转的事件类型并集。

新增子类型时（如 ``BackgroundTaskDoneEvent`` / ``EnvSignalEvent``）加入 union，
:class:`AgentRuntime._dispatch` 加 isinstance 分支即可，不破现有契约。
"""
