"""``LLMStreamEvent``：:meth:`LLMClient.stream` 的流式输出协议。

discriminated union（按 ``type`` 字段分派）。本期承载 4 类事件：

- :class:`LLMTextDelta` — assistant 文本增量
- :class:`LLMToolCallDelta` — tool_calls 增量（同一 ``tool_call_id`` 可能多次到达，需调用方累积）
- :class:`LLMActivity` — 不暴露推理正文的上游活动信号
- :class:`LLMTurnDone` — 本次 LLM 流式响应结束（携带 ``stop_reason``）

**仅 ``llm_providers`` 内部 + ``agent.conversation`` 消费**——不视作引擎对外
公共 API。如未来 ``conversation.py`` 之外的 caller 也要消费，再单独评估升级
为对外公共 API 的事项。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.5.1。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .assistant_content import AssistantContentPart
from .reasoning import ProviderReasoningState
from .request_witness import ProviderRequestRelation, ProviderRequestWitness


@dataclass(frozen=True, repr=False)
class ProviderToolCall:
    """One completed provider tool-call item with its original JSON arguments."""

    index: int
    tool_call_id: str
    tool_name: str
    arguments_json: str
    output_index: int | None = None


@dataclass(frozen=True, repr=False)
class ProviderAssistantItem:
    """One completed assistant text item, distinct even when phases match."""

    item_id: str
    output_index: int
    content: str
    phase: Literal["commentary", "final_answer"] | None = None
    content_parts: tuple[AssistantContentPart, ...] = ()


@dataclass(frozen=True, repr=False)
class ProviderAssistantTurn:
    """Completed assistant response suitable for same-run provider replay."""

    content: str | None
    tool_calls: tuple[ProviderToolCall, ...] = ()
    reasoning_state: ProviderReasoningState | None = None
    text_items: tuple[ProviderAssistantItem, ...] = ()

    def __repr__(self) -> str:
        return (
            "ProviderAssistantTurn("
            f"content_chars={len(self.content or '')}, "
            f"tool_calls={len(self.tool_calls)}, "
            f"reasoning={self.reasoning_state is not None})"
        )

    def to_openai_message(self) -> dict[str, object]:
        message: dict[str, object] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": item.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": item.tool_name,
                        "arguments": item.arguments_json,
                    },
                }
                for item in self.tool_calls
            ]
        state = self.reasoning_state
        if state is not None:
            if state.format == "reasoning_content" and isinstance(state.payload, str):
                message["reasoning_content"] = state.payload
            elif state.format == "reasoning_details" and isinstance(state.payload, tuple):
                message["reasoning_details"] = [dict(item) for item in state.payload]
        return message

    def to_openai_messages(self) -> list[dict[str, object]]:
        """Lossless multi-item replay; the singular method is a legacy aggregate."""
        if not self.text_items:
            return [self.to_openai_message()]
        group = self.text_items[0].item_id
        ordered: list[tuple[int, dict[str, object]]] = []
        for item in self.text_items:
            message: dict[str, object] = {"role": "assistant", "content": item.content}
            if item.phase is not None:
                message["phase"] = item.phase
            ordered.append((item.output_index, message))
        tail = max(item.output_index for item in self.text_items) + 1
        for call in self.tool_calls:
            ordered.append(
                (
                    call.output_index if call.output_index is not None else tail,
                    ProviderAssistantTurn(None, (call,)).to_openai_message(),
                )
            )
        messages = [message for _, message in sorted(ordered, key=lambda item: item[0])]
        for message in messages:
            message["_assistant_response_id"] = group
        reasoning = ProviderAssistantTurn(
            None, reasoning_state=self.reasoning_state
        ).to_openai_message()
        for key in ("reasoning_content", "reasoning_details"):
            if key in reasoning:
                messages[0][key] = reasoning[key]
        return messages


@dataclass(frozen=True)
class LLMTextDelta:
    """assistant 文本增量。"""

    text: str = ""
    type: Literal["text_delta"] = "text_delta"
    phase: Literal["commentary", "final_answer"] | None = None
    item_id: str | None = None
    output_index: int | None = None


@dataclass(frozen=True)
class LLMAssistantItemStart:
    """Provider-neutral assistant item boundary; not visible output itself."""

    item_id: str
    output_index: int
    phase: Literal["commentary", "final_answer"] | None = None
    type: Literal["assistant_item_start"] = "assistant_item_start"


@dataclass(frozen=True)
class LLMAssistantItemDone:
    item_id: str
    output_index: int
    phase: Literal["commentary", "final_answer"] | None = None
    type: Literal["assistant_item_done"] = "assistant_item_done"


@dataclass(frozen=True)
class LLMToolCallDelta:
    """tool_calls 增量（OpenAI 协议风格）。

    同一个 ``tool_call_id`` 可能多次到达（一次首发带 ``id`` / ``name``，
    后续仅带 ``args_json_delta`` 字符串片段）；调用方需要按 ``index`` /
    ``tool_call_id`` 累积，最后 ``json.loads`` 整合后的字符串得到 args 字典。

    Attributes:
        index: tool_calls 数组里的索引（同一 turn 可能并行多个 tool_call）。
        tool_call_id: LLM 给的调用 ID；首次到达时给出，后续片段可能不带（用 index 关联）。
        tool_name: 工具名；首次到达时给出，后续片段可能不带。
        args_json_delta: 入参 JSON 字符串的增量片段。
    """

    index: int = 0
    tool_call_id: str = ""
    tool_name: str = ""
    args_json_delta: str = ""
    type: Literal["tool_call_delta"] = "tool_call_delta"


@dataclass(frozen=True)
class LLMRetryScheduled:
    """Retry the uncommitted response; consumers must reset attempt buffers."""

    attempt: int = 1
    max_attempts: int = 1
    delay_seconds: float = 0.0
    type: Literal["retry_scheduled"] = "retry_scheduled"


@dataclass(frozen=True)
class LLMActivity:
    """上游仍在产出不可见内容的瞬态活动信号，不包含推理正文。"""

    kind: Literal["reasoning", "processing"] = "processing"
    type: Literal["activity"] = "activity"


@dataclass(frozen=True)
class LLMUsage:
    """单次 LLM 调用的 token 用量（009 起新增）。

    作为上下文管理（009）token 估算的"真实锚点"——流式路径来自带
    ``stream_options={"include_usage": True}`` 的尾 chunk，非流式来自
    ``response.usage``。Provider 不透出时调用方应拿到 ``None``、退化到字符估算。

    Attributes:
        prompt_tokens: 输入（prompt）token 数。009 token 估算的核心锚点。
        completion_tokens: 输出（completion）token 数。
        total_tokens: 总 token 数（通常 = prompt + completion）。
        cached_prompt_tokens: Provider 报告的缓存命中输入 token；未提供时为
            ``None``，与明确报告 ``0`` 区分。
        cache_miss_prompt_tokens: Provider 报告或可由输入减命中数推导的缓存未命中
            token；缓存信息不可用时为 ``None``。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int | None = None
    cache_write_prompt_tokens: int | None = None
    input_other_prompt_tokens: int | None = None
    cache_miss_prompt_tokens: int | None = None
    usage_source: str | None = field(default=None, compare=False)
    input_total_prompt_tokens_source: str | None = field(default=None, compare=False)
    completion_tokens_source: str | None = field(default=None, compare=False)
    total_tokens_source: str | None = field(default=None, compare=False)
    cached_prompt_tokens_source: str | None = field(default=None, compare=False)
    cache_write_prompt_tokens_source: str | None = field(default=None, compare=False)
    input_other_prompt_tokens_source: str | None = field(default=None, compare=False)
    cache_miss_prompt_tokens_source: str | None = field(default=None, compare=False)
    input_token_accounting: str = field(default="unavailable", compare=False)
    input_other_prompt_tokens_derived: bool = field(default=False, compare=False)
    cache_miss_prompt_tokens_derived: bool = field(default=False, compare=False)

    @property
    def cache_reporting_state(self) -> str:
        """Keep an unavailable cache counter distinct from an explicit zero."""
        if self.cached_prompt_tokens is None:
            return "unavailable"
        if self.cached_prompt_tokens == 0:
            return "reported_zero"
        return "reported_nonzero"

    @property
    def input_buckets_reconciled(self) -> bool:
        """Whether all mutually-exclusive input buckets reconcile to the total."""

        values = (
            self.cached_prompt_tokens,
            self.cache_write_prompt_tokens,
            self.input_other_prompt_tokens,
        )
        if any(value is None for value in values):
            return False
        return sum(value for value in values if value is not None) == self.prompt_tokens


@dataclass(frozen=True)
class LLMResponseDiagnostics:
    """Content-free provider response metadata and transport milestones."""

    transport: str = "unknown"
    request_bytes: int | None = None
    request_fingerprint: str | None = None
    request_relation: ProviderRequestRelation = "unavailable"
    common_prefix_items: int | None = None
    previous_request_items: int | None = None
    first_divergence_index: int | None = None
    first_divergence_role: str | None = None
    first_divergence_domain: str | None = None
    first_divergence_previous_domain: str | None = None
    first_divergence_current_domain: str | None = None
    contributor_mapping_precision: str = "generic"
    lost_prefix_items: int | None = None
    request_witness: ProviderRequestWitness | None = field(default=None, repr=False, compare=False)
    stream_open_ms: int | None = None
    first_chunk_ms: int | None = None
    first_reasoning_delta_ms: int | None = None
    provider_request_id: str | None = None
    response_id: str | None = None
    response_model: str | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None
    response_cost: float | None = None
    response_cost_source: str | None = None
    cache_observation: dict[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class LLMTransportDiagnostic:
    """One content-free milestone emitted through the optional trace callback."""

    phase: str
    attempt: int
    attempt_elapsed_ms: int
    transport: str
    request_bytes: int | None = None
    request_fingerprint: str | None = None
    request_relation: ProviderRequestRelation = "unavailable"
    common_prefix_items: int | None = None
    previous_request_items: int | None = None
    first_divergence_index: int | None = None
    first_divergence_role: str | None = None
    first_divergence_domain: str | None = None
    first_divergence_previous_domain: str | None = None
    first_divergence_current_domain: str | None = None
    contributor_mapping_precision: str = "generic"
    lost_prefix_items: int | None = None
    request_witness: ProviderRequestWitness | None = field(default=None, repr=False, compare=False)
    provider_request_id: str | None = None
    response_id: str | None = None
    response_model: str | None = None
    system_fingerprint: str | None = None
    service_tier: str | None = None
    provider_error_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class LLMTurnDone:
    """本次 LLM 流式响应结束。

    Attributes:
        stop_reason: 结束原因，标准化为以下值之一：

            - ``"end_turn"`` — LLM 自然结束（OpenAI ``finish_reason="stop"``）
            - ``"tool_use"`` — 决定调用工具（OpenAI ``finish_reason="tool_calls"``）
            - ``"max_tokens"`` — 触达 max_tokens 上限
            - ``"other"`` — 其它（length / content_filter 等罕见情况）
        usage: 本次调用的真实 token 用量（009 起新增）。仅当 provider 透出时
            非空——需 ``stream`` 调用方开启 ``stream_options.include_usage``
            且 provider 在尾 chunk 返回 usage；不支持的 provider 为 ``None``，
            上层（009 ``Conversation`` / ``budget``）退化到字符估算。
    """

    stop_reason: str = ""
    usage: LLMUsage | None = None
    diagnostics: LLMResponseDiagnostics | None = None
    reasoning_state: ProviderReasoningState | None = None
    assistant_turn: ProviderAssistantTurn | None = field(default=None, repr=False, compare=False)
    type: Literal["done"] = "done"


LLMStreamEvent = (
    LLMTextDelta
    | LLMToolCallDelta
    | LLMRetryScheduled
    | LLMActivity
    | LLMTurnDone
    | LLMAssistantItemStart
    | LLMAssistantItemDone
)
"""所有 :meth:`LLMClient.stream` 可能 yield 的事件类型并集。"""
