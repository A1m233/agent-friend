"""``FifoContextManager``：防爆窗兜底的零 LLM 成本 FIFO 截断策略。

定位（requirement R-2.2）：**仅作摘要不可用时的安全网**——超窗口预算时从最老的
历史开始丢，使请求落回预算内，不再直接抛 :class:`llm_providers.LLMBadRequestError`
中断对话。正常情况由 M3 摘要主导；M3 的 circuit breaker 跳闸后退化到本策略。

关键约束：**只在 user 轮边界裁剪**。``session.messages`` 把一次工具调用循环投影成
``assistant``（带 ``meta["tool_calls"]``）+ 若干 ``role="tool"`` 结果消息，这些必须
成组出现（OpenAI 协议要求 tool 结果紧跟其 assistant 请求）。若在组中间裁断会产出
孤儿 tool 消息、触发 400。每个 ``user`` 消息开启一个新轮，其后续 assistant/tool 全在
本轮内、下一个 ``user`` 之前——故按"完整 user 轮"裁剪天然保组完整。

保留策略按 token 预算选择连续的最新完整 turn 后缀；当前工具续轮至少保留最后一轮，
单个巨大 tool result 则只在模型视图中截断。仍无法满足硬边界时明确失败，不发送已知
超预算请求。详见 063 技术方案。
"""

from __future__ import annotations

from ..attachments import AttachmentRef
from ..messages import Message
from .budget import estimate_request_tokens
from .errors import ContextBaseOverBudgetError, ContextInputTooLargeError
from .protocol import BuildResult, RuntimeContext, assemble_messages
from .tool_projection import project_tool_results
from .turns import recent_turn_start_candidates

RECENT_PROTECT_TURNS = 2
"""旧调用方兼容常量；063 起保留轮数完全由 token 预算决定。"""


class FifoContextManager:
    """按 token 预算从最老轮开始丢的 FIFO 兜底策略（009 M2）。"""

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        new_user_attachments: list[AttachmentRef] | None = None,
        extra_context: list[Message] | None = None,
        turn_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult:
        full = assemble_messages(
            history=history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            new_user_attachments=new_user_attachments,
            extra_context=extra_context,
            turn_context=turn_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )

        # runtime=None → 退化为 Naive（不做预算判断，与不变量 6 一致）
        if runtime is None:
            return BuildResult(messages=full)

        threshold = runtime.budget.trigger_threshold
        projected_history, projection = project_tool_results(history, runtime)
        full = assemble_messages(
            history=projected_history,
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            new_user_attachments=new_user_attachments,
            extra_context=extra_context,
            turn_context=turn_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )
        full_estimate = estimate_request_tokens(full, runtime)
        if full_estimate <= threshold:
            return BuildResult(
                messages=full,
                notes={
                    "context_strategy": "fifo",
                    "token_estimate": full_estimate,
                    "tool_results_truncated": projection.truncated,
                    "tool_results_omitted": 0,
                    "final_strategy": "full",
                },
            )

        # 超阈值：从最老 user 轮开始丢，直到落回预算内。
        chosen_start = 0
        chosen_messages = full
        chosen_estimate = full_estimate
        candidates = recent_turn_start_candidates(projected_history)
        has_ephemeral_current = any(
            value is not None for value in (new_user_input, trailing_user, trailing_system)
        )
        if not has_ephemeral_current and candidates and candidates[-1] == len(projected_history):
            candidates = candidates[:-1]
        for start in candidates:
            truncated = projected_history[start:]
            messages = assemble_messages(
                history=truncated,
                system_prompt=system_prompt,
                new_user_input=new_user_input,
                new_user_attachments=new_user_attachments,
                extra_context=extra_context,
                turn_context=turn_context,
                trailing_user=trailing_user,
                trailing_system=trailing_system,
            )
            chosen_start = start
            chosen_messages = messages
            chosen_estimate = estimate_request_tokens(messages, runtime)
            if chosen_estimate <= threshold:
                break

        emergency_messages, emergency_projection = project_tool_results(
            chosen_messages,
            runtime,
            emergency=True,
        )
        chosen_messages = emergency_messages
        chosen_estimate = estimate_request_tokens(chosen_messages, runtime)
        if chosen_estimate > threshold:
            fixed_messages = assemble_messages(
                history=[],
                system_prompt=system_prompt,
                extra_context=extra_context,
                turn_context=turn_context,
                trailing_system=trailing_system,
            )
            fixed_estimate = estimate_request_tokens(fixed_messages, runtime)
            if fixed_estimate > threshold:
                raise ContextBaseOverBudgetError(
                    "系统提示词、运行上下文与工具定义已超过模型预算，请调整模型配置后重试。"
                )
            raise ContextInputTooLargeError(
                "当前输入在安全收缩后仍超过模型上下文预算，请缩短输入或减少附件后重试。"
            )

        return BuildResult(
            messages=chosen_messages,
            dropped_count=chosen_start,
            notes={
                "context_strategy": "fifo",
                "fifo_truncated": chosen_start > 0,
                "token_estimate": chosen_estimate,
                "over_budget_after_truncation": False,
                "tool_results_truncated": projection.truncated,
                "tool_results_omitted": emergency_projection.omitted,
                "final_strategy": "fifo",
            },
        )
