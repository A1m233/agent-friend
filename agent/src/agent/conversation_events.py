"""``ConversationEvent``：:meth:`Conversation.stream` 的流式输出协议。

discriminated union（按 ``type`` 字段分派）。

- :class:`TextDelta` — assistant 文本增量
- :class:`ToolCallRequest` — assistant 决定调用某工具（已经累积出完整 tool_call）
- :class:`ToolCallResult` — 工具执行完毕
- :class:`SkillActivationStatus` — 调用方预选 Skill 的加载结果
- :class:`TurnDone` — 本轮（含可能的多轮 tool 调用）正常结束

未来扩展（语音 / 表情 / 状态信号等）通过新增 dataclass + 加进 :data:`ConversationEvent`
union 实现，**消费方对未知 type 应优雅 fall-through 忽略**——这是协议级前向兼容
约定。

错误处理走 ``raise`` 路径（与 001/002 已建立的体系一致）：LLM 失败时
:meth:`Conversation.stream` 抛 :class:`llm_providers.LLMError` 子类；
工具的业务级失败已通过 :class:`ToolCallResult` ``is_error=True`` 表达，
不会另外 yield 错误事件。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.4。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextDelta:
    """assistant 文本增量。

    Attributes:
        text: 本片段文本（可能从单字到几十字不等）。
    """

    text: str = ""
    type: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True)
class ToolCallRequest:
    """assistant 决定调用某工具。

    在 LLM 流式响应里 ``tool_calls`` delta 已累积出完整 tool_call 后由
    :class:`Conversation` yield 出来，让上层观测者（CLI / 日志）能在执行前
    看到"AI 想调什么、传什么参数"。

    Attributes:
        tool_call_id: LLM 给的调用 ID（OpenAI 协议的 ``tool_calls[*].id``）；
            后续 :class:`ToolCallResult` 通过同一 id 关联。
        tool_name: 工具名（与 :class:`agent.tools.Tool.name` 对应）。
        args: 入参字典（已从 LLM 的 JSON 字符串解析）。
    """

    tool_call_id: str = ""
    event_tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    origin: Literal["model", "deterministic_history_router"] = "model"
    type: Literal["tool_call_request"] = "tool_call_request"


@dataclass(frozen=True)
class ToolCallResult:
    """工具执行完毕。

    与前面同 ``tool_call_id`` 的 :class:`ToolCallRequest` 配对。

    Attributes:
        tool_call_id: 与 :class:`ToolCallRequest` 同。
        tool_name: 与 :class:`ToolCallRequest` 同。
        text: 喂回 LLM 的文本（来自 :class:`agent.tools.ToolResult.text`）。
        is_error: ``True`` 表示业务级失败；调用循环不会因此终止——
            会把这条作为 ``role="tool"`` 消息回喂 LLM 让它决定如何处理。
        duration_seconds: 工具执行耗时（秒）；供观测/调试用。
    """

    tool_call_id: str = ""
    event_tool_call_id: str = ""
    tool_name: str = ""
    text: str = ""
    is_error: bool = False
    duration_seconds: float = 0.0
    origin: Literal["model", "deterministic_history_router"] = "model"
    type: Literal["tool_call_result"] = "tool_call_result"


@dataclass(frozen=True)
class ModelResponseStatus:
    """当前模型请求的瞬态进度；只供实时 UI，不持久化到 session。"""

    phase: Literal["waiting", "retrying"] = "waiting"
    after_tool: bool = False
    attempt: int = 1
    max_attempts: int = 1
    type: Literal["model_response_status"] = "model_response_status"


@dataclass(frozen=True)
class SkillActivationStatus:
    """调用方为当前用户轮预选 Skill 后产生的瞬态加载状态。

    模型自主激活走正常的 ``skill_read`` 工具事件；这个事件只补齐调用方预选
    路径的可见性。成功读取前不得发 ``activated``，失败也必须原样可见。
    """

    status: Literal["activated", "error"] = "error"
    skill_id: str = ""
    name: str = ""
    source: str = ""
    invocation_kind: Literal["caller"] = "caller"
    reason: str = ""
    error_code: str = ""
    error_message: str = ""
    order: int = 0
    type: Literal["skill_activation_status"] = "skill_activation_status"


@dataclass(frozen=True)
class TurnDone:
    """本轮对话正常结束。

    Attributes:
        stop_reason: 结束原因。常见值：

            - ``"end_turn"`` — LLM 主动结束（无工具调用 或 整合工具结果后结束）
            - ``"budget_exhausted"`` — 显式运行预算耗尽（已走收尾兜底逻辑）
            - ``"no_progress"`` — 连续重复失败触发无进展保护
        total_tool_calls: 本轮累计执行的工具调用次数（含失败）。
    """

    stop_reason: str = ""
    total_tool_calls: int = 0
    model_continuations: int = 0
    elapsed_seconds: float = 0.0
    budget_dimension: str | None = None
    type: Literal["done"] = "done"


ConversationEvent = (
    TextDelta
    | ToolCallRequest
    | ToolCallResult
    | ModelResponseStatus
    | SkillActivationStatus
    | TurnDone
)
"""所有 :meth:`Conversation.stream` 可能 yield 的事件类型并集。

形态承诺：**只增不减**——新增子类型不算破坏；删除/重命名既有子类型才算破坏。
"""
