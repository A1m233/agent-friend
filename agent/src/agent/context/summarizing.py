"""``SummarizingContextManager``：摘要压缩主策略（009 M3）。

把长历史蒸馏成结构化 summary，腾出窗口同时尽量不丢关键信息。核心行为：

- **平时折叠**：用 ``runtime.prior_summary``（由 ``Conversation`` 从
  :meth:`Session.latest_compaction` 派生）把已覆盖的旧消息替换为一条 summary
  system 消息，省 token；估算仍在阈值内则直接返回。
- **超阈值触发摘要**：把"较旧部分"（保留最近 N 个 user 轮逐字不折，复用 M2 的
  user 轮边界保组逻辑）重新蒸馏成 summary，并生成 :class:`CompactionRecord` 交给
  ``Conversation`` 落 ``compaction`` 事件。
- **全量重摘不做 summary-of-summary**（design §6.3）：摘要输入用**全量原始**的较旧
  部分，不在 prior_summary 上增量叠加——避免"摘要的摘要"累积失真。仅当较旧部分
  本身就超过摘要输入预算（极端长会话，摘要调用自己塞不下）时，才退回增量模式
  （prior_summary + 其后原始消息）。
- **circuit breaker**：摘要连续失败 :data:`MAX_CONSECUTIVE_FAILURES` 次跳闸，退化到
  注入的 FIFO 兜底，不反复重试拖垮对话（design §6.1 / Q-6）。状态 per-session
  （工厂为每个会话产独立实例，见 design §7）。

⚠️ 摘要是**真实 LLM 调用**，运行时触发受 ``llm-api-confirm`` 约束。

详见 docs/requirements/009-engine-context-management/design.md §6。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..messages import Message
from .budget import estimate_tokens
from .fifo import FifoContextManager
from .protocol import (
    BuildResult,
    CompactionRecord,
    ContextManager,
    PriorSummary,
    RuntimeContext,
    assemble_messages,
)
from .summary_prompt import (
    build_summary_messages,
    render_summary_as_context,
    render_transcript,
    strip_analysis,
)

MAX_CONSECUTIVE_FAILURES = 3
"""摘要连续失败多少次后跳闸退化到 FIFO（design §6.1 / Q-6）。"""

SUMMARY_RECENT_TAIL_TURNS = 2
"""触发摘要时保留最近多少个 user 轮**逐字不折**（保组完整 + 近期上下文连续性）。

按 user 轮边界切，保证进行中的工具组（assistant tool_calls + tool 结果）不被折断，
也给摘要后的对话留逐字近况。较旧的部分才被蒸馏进 summary。
"""


@dataclass(frozen=True)
class _AssembleParts:
    """``build_messages`` 里"拼什么内容"的固定成分（区别于"裁/折"的 history）。"""

    system_prompt: str
    new_user_input: str | None
    extra_context: list[Message] | None
    trailing_user: str | None
    trailing_system: str | None

    def assemble(self, history: list[Message]) -> list[Message]:
        return assemble_messages(
            history=history,
            system_prompt=self.system_prompt,
            new_user_input=self.new_user_input,
            extra_context=self.extra_context,
            trailing_user=self.trailing_user,
            trailing_system=self.trailing_system,
        )


class SummarizingContextManager:
    """摘要压缩主策略（009 M3）。

    Args:
        fallback: 摘要不可用（跳闸 / 无可压缩较旧部分）时的兜底策略，默认
            :class:`FifoContextManager`。
        max_failures: circuit breaker 阈值，默认 :data:`MAX_CONSECUTIVE_FAILURES`。
        recent_tail_turns: 触发摘要时保留逐字的最近 user 轮数，默认
            :data:`SUMMARY_RECENT_TAIL_TURNS`。
    """

    def __init__(
        self,
        fallback: ContextManager | None = None,
        *,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
        recent_tail_turns: int = SUMMARY_RECENT_TAIL_TURNS,
    ) -> None:
        self._fallback: ContextManager = fallback or FifoContextManager()
        self._max_failures = max_failures
        self._recent_tail_turns = recent_tail_turns
        self._consecutive_failures = 0
        self._tripped = False

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
        parts = _AssembleParts(
            system_prompt=system_prompt,
            new_user_input=new_user_input,
            extra_context=extra_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
        )

        # runtime=None → 退化 Naive（不做预算判断，不变量 6）
        if runtime is None:
            return BuildResult(messages=parts.assemble(history))

        # 已跳闸：直接走 FIFO 兜底
        if self._tripped:
            return self._fallback_result(history, parts, runtime, reason="circuit_tripped")

        threshold = runtime.budget.trigger_threshold
        prior = runtime.prior_summary

        # 平时：用 prior_summary 折叠展示，估算未超阈值则直接返回
        folded_msgs = parts.assemble(self._fold_with_prior(history, prior))
        est = estimate_tokens(folded_msgs)
        if est <= threshold:
            return BuildResult(
                messages=folded_msgs,
                notes={
                    "context_strategy": "summarizing",
                    "folded": prior is not None,
                    "token_estimate": est,
                },
            )

        # 超阈值：重新蒸馏较旧部分
        return self._compact(history, parts, runtime, prior, threshold)

    # ----- 触发摘要 -----

    def _compact(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        prior: PriorSummary | None,
        threshold: int,
    ) -> BuildResult:
        cut = self._recent_tail_cut(history)
        if cut <= 0:
            # 没有可压缩的较旧部分（全在保护 tail / 无 user 边界）→ 本轮退 FIFO 兜底，
            # 不计失败、不跳闸（这不是摘要失败，是无从压缩）。
            return self._fallback_result(history, parts, runtime, reason="nothing_to_compact")

        older = history[:cut]
        tail = history[cut:]
        summary_text, input_kind = self._summary_input(older, prior, threshold)

        try:
            raw = runtime.llm_client.complete(build_summary_messages(summary_text))
        except Exception:
            return self._on_failure(history, parts, runtime, reason="llm_error")

        summary = strip_analysis(raw).strip()
        if not summary:
            return self._on_failure(history, parts, runtime, reason="empty_summary")

        # 摘要成功
        self._consecutive_failures = 0
        summary_msg = Message(role="system", content=render_summary_as_context(summary))
        msgs = parts.assemble([summary_msg, *tail])
        record = CompactionRecord(
            summary=summary,
            covered_through_uuid=older[-1].uuid,
            tokens_before=estimate_tokens(parts.assemble(history)),
            tokens_after=estimate_tokens(msgs),
            model=getattr(getattr(runtime.llm_client, "spec", None), "model", ""),
        )
        return BuildResult(
            messages=msgs,
            new_compaction=record,
            notes={
                "context_strategy": "summarizing",
                "compacted": True,
                "summary_input": input_kind,
                "token_estimate": record.tokens_after,
            },
        )

    def _on_failure(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        *,
        reason: str,
    ) -> BuildResult:
        """摘要失败：计数 +1，达阈值跳闸，本轮退 FIFO 兜底。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_failures:
            self._tripped = True
        return self._fallback_result(history, parts, runtime, reason=reason)

    def _fallback_result(
        self,
        history: list[Message],
        parts: _AssembleParts,
        runtime: RuntimeContext,
        *,
        reason: str,
    ) -> BuildResult:
        """委托 FIFO 兜底并打上可观测标记（跳闸退化 / 无从压缩 / 摘要失败）。"""
        result = self._fallback.build_messages(
            history=history,
            system_prompt=parts.system_prompt,
            new_user_input=parts.new_user_input,
            extra_context=parts.extra_context,
            trailing_user=parts.trailing_user,
            trailing_system=parts.trailing_system,
            runtime=runtime,
        )
        result.notes.setdefault("context_strategy", "summarizing")
        result.notes["fell_back"] = True
        result.notes["fallback_reason"] = reason
        result.notes["circuit_tripped"] = self._tripped
        return result

    # ----- 折叠 / 切分 / 摘要输入 -----

    def _fold_with_prior(self, history: list[Message], prior: PriorSummary | None) -> list[Message]:
        """用 prior_summary 把已覆盖的旧消息替换为一条 summary system 消息。

        ``covered_through_uuid`` 不在当前 history 中时（异常）→ 不折叠、返回原样。
        """
        if prior is None:
            return list(history)
        idx = self._index_of_uuid(history, prior.covered_through_uuid)
        if idx is None:
            return list(history)
        summary_msg = Message(role="system", content=render_summary_as_context(prior.summary))
        return [summary_msg, *history[idx + 1 :]]

    def _summary_input(
        self, older: list[Message], prior: PriorSummary | None, threshold: int
    ) -> tuple[str, str]:
        """决定摘要输入文本：默认全量原始较旧部分；极端超长退回增量。

        Returns:
            ``(转录文本, "full" | "incremental")``。
        """
        full_text = render_transcript(older)
        full_est = estimate_tokens([Message(role="user", content=full_text)])
        if prior is None or full_est <= threshold:
            return full_text, "full"

        # 极端退化：较旧部分自己都超摘要输入预算 → prior_summary + 其后原始增量
        idx = self._index_of_uuid(older, prior.covered_through_uuid)
        after = older[idx + 1 :] if idx is not None else older
        text = f"[此前摘要]\n{prior.summary}\n\n[此后新增对话]\n{render_transcript(after)}"
        return text, "incremental"

    def _recent_tail_cut(self, history: list[Message]) -> int:
        """返回"保护 tail 起点"下标：保留最近 N 个 user 轮，其前为可压缩较旧部分。

        ``<=0`` 表示没有可压缩的较旧部分（user 轮数 <= N，或无 user 消息）。
        切点取 user 消息下标 → 保证不折断工具组（assistant tool_calls + tool 结果
        总在同一个 user 轮内、下一个 user 之前）。
        """
        user_indices = [i for i, m in enumerate(history) if m.role == "user"]
        if len(user_indices) <= self._recent_tail_turns:
            return 0
        return user_indices[-self._recent_tail_turns]

    @staticmethod
    def _index_of_uuid(messages: list[Message], uuid: str) -> int | None:
        for i, m in enumerate(messages):
            if m.uuid == uuid:
                return i
        return None
