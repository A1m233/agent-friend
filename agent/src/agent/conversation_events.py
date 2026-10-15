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

from .attachments import AttachmentRef


@dataclass(frozen=True)
class TextDelta:
    """assistant 文本增量。

    Attributes:
        text: 本片段文本（可能从单字到几十字不等）。
    """

    text: str = ""
    type: Literal["text_delta"] = "text_delta"
    message_id: str | None = None
    phase: Literal["commentary", "final_answer", "unknown"] = "unknown"
    item_index: int | None = None


@dataclass(frozen=True)
class AssistantMessageStart:
    message_id: str
    phase: Literal["commentary", "final_answer", "unknown"]
    item_index: int = 0
    type: Literal["assistant_message_start"] = "assistant_message_start"


@dataclass(frozen=True)
class AssistantMessageReset:
    """Retract only uncommitted items from a failed model response attempt."""

    message_ids: tuple[str, ...]
    type: Literal["assistant_message_reset"] = "assistant_message_reset"


@dataclass(frozen=True)
class AssistantMessageEnd:
    message_id: str
    type: Literal["assistant_message_end"] = "assistant_message_end"


@dataclass(frozen=True)
class AssistantMessagePhase:
    """Metadata correction for an existing message, never another text delivery."""

    message_id: str
    phase: Literal["commentary", "final_answer", "unknown"]
    type: Literal["assistant_message_phase"] = "assistant_message_phase"


@dataclass(frozen=True)
class AssistantMessageMetadata:
    message_id: str
    content_parts: list[dict[str, Any]]
    type: Literal["assistant_message_metadata"] = "assistant_message_metadata"


@dataclass(frozen=True)
class AssistantImageStart:
    message_id: str
    item_index: int
    type: Literal["assistant_image_start"] = "assistant_image_start"


@dataclass(frozen=True)
class AssistantImage:
    message_id: str
    item_index: int
    images: tuple[AttachmentRef, ...] = ()
    error_code: str | None = None
    type: Literal["assistant_image"] = "assistant_image"


@dataclass(frozen=True)
class AssistantPhaseStatus:
    run_id: str
    mode: Literal["native", "legacy"]
    validation: Literal["pending", "valid", "missing_final", "conflict"] = "pending"
    type: Literal["assistant_phase"] = "assistant_phase"


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
    arguments_json: str = ""
    arguments_state: Literal["valid_object", "invalid_json", "non_object", "missing"] = (
        "valid_object"
    )
    execution_disposition: Literal["execute", "rejected_before_execution"] = "execute"
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
    executed: bool = True
    authorization_requested: bool = False
    error_code: str = ""
    duration_seconds: float = 0.0
    images: tuple[AttachmentRef, ...] = ()
    origin: Literal["model", "deterministic_history_router"] = "model"
    type: Literal["tool_call_result"] = "tool_call_result"


@dataclass(frozen=True)
class ModelResponseStatus:
    """当前模型请求的瞬态进度；只供实时 UI，不持久化到 session。"""

    phase: Literal["waiting", "reasoning", "processing", "retrying"] = "waiting"
    after_tool: bool = False
    attempt: int = 1
    max_attempts: int = 1
    type: Literal["model_response_status"] = "model_response_status"


@dataclass(frozen=True)
class ContextCompactionStatus:
    """上下文压缩活动的实时生命周期；完成事实由 compaction session event 持久化。"""

    activity_id: str = ""
    phase: Literal["started", "completed", "discarded"] = "started"
    type: Literal["context_compaction_status"] = "context_compaction_status"


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
    """本轮对话的显式终态。

    Attributes:
        stop_reason: 结束原因。常见值：

            - ``"end_turn"`` — LLM 主动结束（无工具调用 或 整合工具结果后结束）
            - ``"budget_exhausted"`` — 显式运行预算耗尽（已走收尾兜底逻辑）
        total_tool_calls: 本轮累计执行的工具调用次数（含失败）。
    """

    status: Literal["completed", "interrupted", "failed"] = "completed"
    stop_reason: str = ""
    total_tool_calls: int = 0
    tool_call_items: int = 0
    model_calls: int = 0
    model_continuations: int = 0
    elapsed_seconds: float = 0.0
    budget_dimension: str | None = None
    type: Literal["done"] = "done"


ConversationEvent = (
    TextDelta
    | AssistantImageStart
    | AssistantImage
    | AssistantMessageStart
    | AssistantMessageEnd
    | AssistantMessageReset
    | AssistantMessagePhase
    | AssistantMessageMetadata
    | AssistantPhaseStatus
    | ToolCallRequest
    | ToolCallResult
    | ModelResponseStatus
    | ContextCompactionStatus
    | SkillActivationStatus
    | TurnDone
)
"""所有 :meth:`Conversation.stream` 可能 yield 的事件类型并集。

形态承诺：**只增不减**——新增子类型不算破坏；删除/重命名既有子类型才算破坏。
"""
