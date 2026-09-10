"""token 度量与预算推导（009 M1）。

职责：

- 估算"当前要发出的上下文"的 token 占用（偏保守上界，宁可早触发）
- 按当前 model 的 context window **动态推导**触发阈值（不写死固定值）
- 产出 :class:`BudgetSnapshot` 供 M2/M3 共享

本模块**不 import litellm**——窗口数值由 ``llm_providers`` 经
``LanguageModel.context_window`` 提供（保持分层隔离，见 009 design §2.2）。

详见 docs/requirements/009-engine-context-management/design.md §3。
"""

from __future__ import annotations

import json
import math
from typing import Any

from ..message_references import render_user_content_for_model
from ..messages import Message
from .protocol import BudgetSnapshot, RuntimeContext

CHARS_TO_TOKENS = 0.75
"""字符 → token 的保守系数（009 design §3.3 / Q-4）。

cc 对英文用 ``/4``（≈0.25 token/char）够准，但中文 1 字常 ≈1~2 token，0.25 会
严重低估。取 0.75 作偏上界的保守值——宁可估多早触发，不可估少漏触发撑爆。
后续可用真实 usage 锚点对照校准。
"""

OUTPUT_RESERVE_RATIO = 0.1
"""给本轮输出预留的窗口占比（009 design §3.3）。"""

BUFFER_RATIO = 0.1
"""估算误差缓冲的窗口占比（009 design §3.3）。"""

IMAGE_ATTACHMENT_TOKENS = 1536
"""图片输入的保守固定预算；具体视觉切片成本由 provider 决定。"""

DOCUMENT_BYTES_TO_TOKENS = 0.25
DOCUMENT_ATTACHMENT_TOKEN_CAP = 64_000
"""文档预估只用于压缩预检；实际抽取文本会在 provider 适配层再次按窗口校验。"""


def estimate_tokens(messages: list[Message]) -> int:
    """对消息序列做保守的 token 估算（按字符数 × :data:`CHARS_TO_TOKENS`）。

    M1 用于 observability；M2/M3 用于"是否超阈值"的判断。偏上界：用整段字符估算，
    不依赖 provider 是否透出 usage，任何场景都可用。

    Note:
        本函数只负责无锚点时的全量降级和独立片段估算。真实输入锚点由
        usage_anchor 校验请求身份，并经 RuntimeContext.request_estimator 接入。
    """
    total_chars = 0
    for msg in messages:
        content = (
            render_user_content_for_model(msg.content, msg.references)
            if msg.role == "user" and msg.references
            else msg.content
        )
        total_chars += len(content or "")
        # tool_calls 等结构化 meta 也占 token，粗略计入其字符串化长度
        tool_calls = msg.meta.get("tool_calls") if msg.meta else None
        if tool_calls:
            total_chars += len(str(tool_calls))
    attachment_tokens = 0
    for msg in messages:
        for attachment in msg.attachments:
            if attachment.kind == "image":
                attachment_tokens += IMAGE_ATTACHMENT_TOKENS
            else:
                attachment_tokens += min(
                    DOCUMENT_ATTACHMENT_TOKEN_CAP,
                    max(256, int(attachment.size_bytes * DOCUMENT_BYTES_TO_TOKENS)),
                )
    return int(total_chars * CHARS_TO_TOKENS) + attachment_tokens


def estimate_tool_schema_tokens(tool_specs: list[dict[str, Any]] | None) -> int:
    """估算本轮实际发送的 tool schema 成本。"""
    if not tool_specs:
        return 0
    rendered = json.dumps(tool_specs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return int(len(rendered) * CHARS_TO_TOKENS)


def estimate_request_tokens(messages: list[Message], runtime: RuntimeContext) -> int:
    """按当前模型 usage 校准和 tool schema 开销估算完整请求输入。"""
    if runtime.request_estimator is not None:
        return runtime.request_estimator(messages)
    raw = estimate_tokens(messages) + runtime.request_overhead_tokens
    return math.ceil(raw * runtime.calibration_factor)


def make_budget_snapshot(
    effective_window: int,
    last_input_tokens: int | None,
    *,
    emergency_ratio: float = 1.0,
) -> BudgetSnapshot:
    """按 model 窗口动态推导本轮预算快照（009 R-1.3）。

    Args:
        effective_window: 当前 model 的有效输入窗口（= ``LanguageModel.context_window``）。
        last_input_tokens: 上轮真实 ``usage.prompt_tokens`` 锚点；无则 ``None``。

    Returns:
        含 ``trigger_threshold`` 派生属性的 :class:`BudgetSnapshot`。
    """
    output_reserve = int(effective_window * OUTPUT_RESERVE_RATIO)
    buffer = int(effective_window * BUFFER_RATIO)
    return BudgetSnapshot(
        effective_window=effective_window,
        last_input_tokens=last_input_tokens,
        output_reserve=output_reserve,
        buffer=buffer,
        emergency_ratio=max(0.1, min(1.0, emergency_ratio)),
    )
