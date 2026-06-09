"""``LLMStreamEvent``：:meth:`LLMClient.stream` 的流式输出协议。

discriminated union（按 ``type`` 字段分派）。本期承载 3 类事件：

- :class:`LLMTextDelta` — assistant 文本增量
- :class:`LLMToolCallDelta` — tool_calls 增量（同一 ``tool_call_id`` 可能多次到达，需调用方累积）
- :class:`LLMTurnDone` — 本次 LLM 流式响应结束（携带 ``stop_reason``）

**仅 ``llm_providers`` 内部 + ``agent.conversation`` 消费**——不视作引擎对外
公共 API。如未来 ``conversation.py`` 之外的 caller 也要消费，再单独评估升级
为对外公共 API 的事项。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.5.1。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LLMTextDelta:
    """assistant 文本增量。"""

    text: str = ""
    type: Literal["text_delta"] = "text_delta"


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
class LLMUsage:
    """单次 LLM 调用的 token 用量（009 起新增）。

    作为上下文管理（009）token 估算的"真实锚点"——流式路径来自带
    ``stream_options={"include_usage": True}`` 的尾 chunk，非流式来自
    ``response.usage``。Provider 不透出时调用方应拿到 ``None``、退化到字符估算。

    Attributes:
        prompt_tokens: 输入（prompt）token 数。009 token 估算的核心锚点。
        completion_tokens: 输出（completion）token 数。
        total_tokens: 总 token 数（通常 = prompt + completion）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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
    type: Literal["done"] = "done"


LLMStreamEvent = LLMTextDelta | LLMToolCallDelta | LLMTurnDone
"""所有 :meth:`LLMClient.stream` 可能 yield 的事件类型并集。"""
