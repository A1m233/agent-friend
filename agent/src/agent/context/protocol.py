"""上下文管理的稳定契约：``ContextManager`` Protocol 与配套数据类。

本模块只放**接口与数据形状**，不放具体策略实现（``naive`` / ``fifo`` /
``summarizing`` 各自成文件）。009 起把原 ``context.py`` 升级为 ``context/`` 包，
按职责拆分。

数据契约（009 design §4）：

- :class:`BuildResult` —— ``build_messages`` 返回值
- :class:`BudgetSnapshot` —— 本轮预算快照（窗口 / 锚点 / 阈值），M2/M3 共享
- :class:`PriorSummary` —— 已落盘的最近折叠点（摘要 + 覆盖范围），平时折叠展示用
- :class:`CompactionRecord` —— 本轮新生成、待 ``Conversation`` 落盘的摘要记录
- :class:`RuntimeContext` —— 运行时依赖载体（预算 / llm_client / 已有 summary）

详见 docs/requirements/009-engine-context-management/design.md §3、§4、§6。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from ..messages import Message

if TYPE_CHECKING:
    from llm_providers import LLMClient


@dataclass(frozen=True)
class BudgetSnapshot:
    """本轮 token 预算快照（009 M1；M2/M3 共享的预算账本）。

    Attributes:
        effective_window: 当前 model 的有效输入窗口（= ``LLMClient.context_window``）。
        last_input_tokens: 上一轮真实 ``usage.prompt_tokens`` 锚点；首轮 / provider
            不透出时为 ``None``，估算退化到纯字符。
        output_reserve: 给本轮输出预留的 token（按窗口比例算）。
        buffer: 估算误差缓冲（按窗口比例算）。
    """

    effective_window: int
    last_input_tokens: int | None
    output_reserve: int
    buffer: int

    @property
    def trigger_threshold(self) -> int:
        """触发裁剪 / 压缩的 token 阈值（窗口 − 输出预留 − 缓冲，下限 0）。"""
        return max(0, self.effective_window - self.output_reserve - self.buffer)


@dataclass(frozen=True)
class PriorSummary:
    """已落盘的最近一次折叠点（009 M3）。

    由 ``Conversation`` 从 :meth:`Session.latest_compaction` 派生，放进
    :class:`RuntimeContext`，供 ``SummarizingContextManager`` 平时折叠展示。

    Attributes:
        summary: 结构化摘要文本。
        covered_through_uuid: 该 summary 覆盖到的最后一条原始事件 uuid（含）。
    """

    summary: str
    covered_through_uuid: str


@dataclass(frozen=True)
class CompactionRecord:
    """本轮新生成、待 ``Conversation`` 落盘为 ``compaction`` 事件的摘要记录（009 M3）。

    context manager 只负责"生成"（含调 LLM），落盘 IO 由 ``Conversation`` 读
    :attr:`BuildResult.new_compaction` 后执行——保持 context manager 无存储副作用。

    Attributes:
        summary: 结构化摘要文本。
        covered_through_uuid: 被折叠覆盖到的最后一条原始事件 uuid（含）。
        tokens_before: 压缩前的 token 估算（observability）。
        tokens_after: 压缩后的 token 估算（observability）。
        model: 生成本摘要所用的 model 名。
    """

    summary: str
    covered_through_uuid: str
    tokens_before: int = 0
    tokens_after: int = 0
    model: str = ""


@dataclass
class RuntimeContext:
    """``build_messages`` 的运行时依赖载体（009 design §4.2）。

    把"靠什么算 / 压"的运行时能力与"拼什么内容"的参数分离；未来新增运行时
    信息直接扩本类字段，不必再改 :meth:`ContextManager.build_messages` 签名。

    每轮由 ``Conversation`` per-call 构造传入（``llm_client`` 在 ``switch_model``
    后会变，故不在 context manager 构造时固化）。

    Attributes:
        budget: 本轮预算快照。
        llm_client: 当前激活的 LLM 客户端（``SummarizingContextManager`` 摘要要用）。
        prior_summary: 已落盘的最近折叠点；``None`` 表示尚无压缩（首压或老会话）。
    """

    budget: BudgetSnapshot
    llm_client: LLMClient
    prior_summary: PriorSummary | None = None


@dataclass
class BuildResult:
    """:meth:`ContextManager.build_messages` 的结构化返回值。

    Attributes:
        messages: 实际要发给 LLM 的消息序列（``system`` 在最前、若有
            ``new_user_input`` / ``trailing_user`` 则在 ``history`` 之后、若有
            ``trailing_system`` 则在最末）。
        dropped_count: 截掉的历史消息条数。:class:`NaiveContextManager` 永远是 0。
        new_compaction: 本轮新生成、待 ``Conversation`` 落盘的摘要记录（009 M3）；
            未触发压缩时为 ``None``。
        notes: 扩展用 observability 字段（如 ``token_estimate`` / ``trigger_threshold``
            / ``compacted`` / ``fell_back``）。
    """

    messages: list[Message]
    dropped_count: int = 0
    new_compaction: CompactionRecord | None = None
    notes: dict[str, Any] = field(default_factory=dict)


class ContextManager(Protocol):
    """上下文管理器的接口契约。

    所有实现都必须遵循以下不变量（009 起放松，向后兼容）：

    1. ``system_prompt`` 不为空时，必须放在 :attr:`BuildResult.messages` 的最前
    2. ``new_user_input`` **若提供**（非 ``None``），放在 ``new_user_input``/``history``
       序列的最后；续轮 / 兜底收尾传 ``None`` 表示本轮无新用户输入
    3. ``extra_context``（memory 注入）若提供，应放在 ``system`` 之后、``history`` 之前
    4. ``trailing_user`` 若提供，rendered 为 ``role="user"`` 消息，位置在
       ``new_user_input`` 之后、``trailing_system`` 之前。语义：仅活在 LLM 视图、
       **不落盘**——主动 source（``system_trigger``）注入触发信号专用（021）。与
       ``new_user_input``（已落盘的真用户输入）语义边界明确，互不替代
    5. ``trailing_system`` 若提供，放在**所有消息之后**（比 ``trailing_user`` 还靠后），
       用于工具调用循环兜底收尾的临时指令
    6. ``history`` 可能被截断 / 折叠，截断的条数记入 :attr:`BuildResult.dropped_count`
    7. ``runtime`` 为 ``None`` 时实现应退化为"不做预算判断"（:class:`NaiveContextManager`
       行为完全不变）；非 ``None`` 时按策略做度量 / 裁剪 / 压缩

    Note:
        009 起 ``history`` 一律传**原始全量** ``session.messages``——折叠（用
        ``runtime.prior_summary`` 把旧段替换为 summary）发生在实现内部，使最近一次
        摘要能基于全量原始重摘、不做 summary-of-summary。详见 design §6.3。
    """

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        extra_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult: ...


def assemble_messages(
    history: list[Message],
    system_prompt: str,
    new_user_input: str | None = None,
    extra_context: list[Message] | None = None,
    trailing_user: str | None = None,
    trailing_system: str | None = None,
) -> list[Message]:
    """按 :class:`ContextManager` 不变量把各成分拼成消息序列（所有策略共用）。

    顺序：``[system?] + extra_context? + history + [new_user_input?] + [trailing_user?] + [trailing_system?]``。
    ``history`` 由各策略在调用前自行裁剪 / 折叠——本函数只负责拼装顺序，不做预算判断。

    ``trailing_user`` 与 ``new_user_input`` 都 rendered 为 ``role="user"``——前者
    给主动 source 注入触发信号用、不落盘（021）；后者是真用户输入、由调用方
    先落盘后再喂。两者语义不同、实际不会同时出现，但参数互不替代。
    """
    messages: list[Message] = []
    if system_prompt:
        messages.append(Message(role="system", content=system_prompt))
    if extra_context:
        messages.extend(extra_context)
    messages.extend(history)
    if new_user_input is not None:
        messages.append(Message(role="user", content=new_user_input))
    if trailing_user is not None:
        messages.append(Message(role="user", content=trailing_user))
    if trailing_system is not None:
        messages.append(Message(role="system", content=trailing_system))
    return messages
