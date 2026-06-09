"""token 度量与预算推导（009 M1）。

职责：

- 估算"当前要发出的上下文"的 token 占用（偏保守上界，宁可早触发）
- 按当前 model 的 context window **动态推导**触发阈值（不写死固定值）
- 产出 :class:`BudgetSnapshot` 供 M2/M3 共享

本模块**不 import litellm**——窗口数值由 ``llm_providers`` 经
``LLMClient.context_window`` 提供（保持分层隔离，见 009 design §2.2）。

详见 docs/requirements/009-engine-context-management/design.md §3。
"""

from __future__ import annotations

from ..messages import Message
from .protocol import BudgetSnapshot

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


def estimate_tokens(messages: list[Message]) -> int:
    """对消息序列做保守的 token 估算（按字符数 × :data:`CHARS_TO_TOKENS`）。

    M1 用于 observability；M2/M3 用于"是否超阈值"的判断。偏上界：用整段字符估算，
    不依赖 provider 是否透出 usage，任何场景都可用。

    Note:
        ``last_input_tokens`` 真实锚点由 :func:`make_budget_snapshot` 收进
        :class:`BudgetSnapshot`，供调用方对照 / 未来校准；本函数保持纯字符、可独立单测。
    """
    total_chars = 0
    for msg in messages:
        total_chars += len(msg.content or "")
        # tool_calls 等结构化 meta 也占 token，粗略计入其字符串化长度
        tool_calls = msg.meta.get("tool_calls") if msg.meta else None
        if tool_calls:
            total_chars += len(str(tool_calls))
    return int(total_chars * CHARS_TO_TOKENS)


def make_budget_snapshot(
    effective_window: int,
    last_input_tokens: int | None,
) -> BudgetSnapshot:
    """按 model 窗口动态推导本轮预算快照（009 R-1.3）。

    Args:
        effective_window: 当前 model 的有效输入窗口（= ``LLMClient.context_window``）。
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
    )
