"""``FifoContextManager``：防爆窗兜底的零 LLM 成本 FIFO 截断策略（009 M2）。

定位（requirement R-2.2）：**仅作摘要不可用时的安全网**——超窗口预算时从最老的
历史开始丢，使请求落回预算内，不再直接抛 :class:`llm_providers.LLMBadRequestError`
中断对话。正常情况由 M3 摘要主导；M3 的 circuit breaker 跳闸后退化到本策略。

关键约束：**只在 user 轮边界裁剪**。``session.messages`` 把一次工具调用循环投影成
``assistant``（带 ``meta["tool_calls"]``）+ 若干 ``role="tool"`` 结果消息，这些必须
成组出现（OpenAI 协议要求 tool 结果紧跟其 assistant 请求）。若在组中间裁断会产出
孤儿 tool 消息、触发 400。每个 ``user`` 消息开启一个新轮，其后续 assistant/tool 全在
本轮内、下一个 ``user`` 之前——故按"完整 user 轮"裁剪天然保组完整。

保留策略（design §5 / Q-3）：按 token 预算裁剪 + 保护最近 :data:`RECENT_PROTECT_TURNS`
个 user 轮（条数下限），避免极端长单条把近期上下文全裁光（那属 L3，本期非目标）。

详见 docs/requirements/009-engine-context-management/design.md §5。
"""

from __future__ import annotations

from ..messages import Message
from .budget import estimate_tokens
from .protocol import BuildResult, RuntimeContext, assemble_messages

RECENT_PROTECT_TURNS = 2
"""裁剪时至少保护的最近 user 轮数（条数下限，design §5 / Q-3）。

纯粹是"别裁过头"的地板：循环一旦把 token 估算压回阈值内就提前停，本常量只在
"连最近若干轮都塞不下"时兜底——此时保留最近轮、宁可略超预算（极端长单轮属 L3）。
``>=1`` 保证当前进行中的轮（工具循环续轮的活跃轮）永不被裁。
"""


class FifoContextManager:
    """按 token 预算从最老轮开始丢的 FIFO 兜底策略（009 M2）。"""

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        extra_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult:
        full = assemble_messages(
            history=history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            extra_context=extra_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )

        # runtime=None → 退化为 Naive（不做预算判断，与不变量 6 一致）
        if runtime is None:
            return BuildResult(messages=full)

        threshold = runtime.budget.trigger_threshold
        full_estimate = estimate_tokens(full)
        if full_estimate <= threshold:
            return BuildResult(
                messages=full,
                notes={"context_strategy": "fifo", "token_estimate": full_estimate},
            )

        # 超阈值：从最老 user 轮开始丢，直到落回预算内或触达保护地板。
        chosen_start = 0
        chosen_messages = full
        chosen_estimate = full_estimate
        for start in self._truncation_candidates(history):
            truncated = history[start:]
            messages = assemble_messages(
                history=truncated,
                system_prompt=system_prompt,
                new_user_input=new_user_input,
                extra_context=extra_context,
                trailing_user=trailing_user,
                trailing_system=trailing_system,
            )
            chosen_start = start
            chosen_messages = messages
            chosen_estimate = estimate_tokens(messages)
            if chosen_estimate <= threshold:
                break

        return BuildResult(
            messages=chosen_messages,
            dropped_count=chosen_start,
            notes={
                "context_strategy": "fifo",
                "fifo_truncated": chosen_start > 0,
                "token_estimate": chosen_estimate,
                # 即便裁到地板仍超阈值，也只能尽力（极端长单轮属 L3，非本期目标）
                "over_budget_after_truncation": chosen_estimate > threshold,
            },
        )

    @staticmethod
    def _truncation_candidates(history: list[Message]) -> list[int]:
        """生成允许的截断起点（升序 = 丢得越来越多），保组完整 + 保护最近 N 轮。

        起点只取 ``user`` 消息下标（每个 user 开启一个完整轮，从此处保留 = 丢掉之前
        所有完整轮，不产生孤儿 tool）。最大允许起点受 :data:`RECENT_PROTECT_TURNS`
        约束：永远保留最近 N 个 user 轮。

        Returns:
            可作为 ``history[start:]`` 的起点下标列表（升序，均 ``> 0``）。
            无 user 消息时返回空列表（没有可安全裁剪的边界）。
        """
        user_indices = [i for i, m in enumerate(history) if m.role == "user"]
        if not user_indices:
            return []
        # 保护最近 N 个 user 轮：最大允许起点 = 倒数第 N 个 user 边界。
        if len(user_indices) <= RECENT_PROTECT_TURNS:
            max_start = user_indices[0]
        else:
            max_start = user_indices[-RECENT_PROTECT_TURNS]
        return [i for i in user_indices if 0 < i <= max_start]
