"""上下文管理的稳定契约：``ContextManager`` Protocol 与配套数据类。

本模块只放**接口与数据形状**，不放具体策略实现（``naive`` / ``fifo`` /
``summarizing`` 各自成文件）。009 起把原 ``context.py`` 升级为 ``context/`` 包，
按职责拆分。

数据契约（009 design §4）：

- :class:`BuildResult` —— ``build_messages`` 返回值
- :class:`BudgetSnapshot` —— 本轮预算快照（窗口 / 锚点 / 阈值），M2/M3 共享
- :class:`PriorSummary` —— 已落盘的最近折叠点（摘要 + 覆盖范围），平时折叠展示用
- :class:`CompactionRecord` —— 本轮新生成、待 ``Conversation`` 落盘的摘要记录
- :class:`ToolContextEditRecord` —— 本轮新生成、待落盘的 tool result 清理记录
- :class:`RuntimeContext` —— 运行时依赖载体（预算 / llm_client / 已有 summary）

详见 docs/requirements/009-engine-context-management/design.md §3、§4、§6。
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from ..attachments import AttachmentRef
from ..messages import Message

if TYPE_CHECKING:
    from llm_providers import LanguageModel

    from .usage_anchor import InputEstimate

MAX_SUMMARY_REQUESTS = 4
MAX_SUMMARY_SEGMENTS = 3


@dataclass
class CompactionControl:
    """One request's bounded recovery cycle, shared with capacity recovery.

    This is transient orchestration, not another task/history store. The caller
    supplies the accepted business user, and replaces the control only after real
    progress or an explicit new attempt. Nested builders must reuse this object.
    """

    mode: Literal["automatic", "compact_then_resume"] = "automatic"
    current_user: Message | None = None
    source_fingerprint: str = ""
    summary_requests: int = 0
    successful_segments: int = 0
    attempted_inputs: set[str] = field(default_factory=set)
    candidate_fingerprint: str = ""
    summarized_source: tuple[str, ...] = ()
    summary_text: str = ""
    summary_goal_fingerprint: str = ""
    summary_output_retry_used: bool = False


@dataclass(frozen=True)
class BudgetSnapshot:
    """本轮 token 预算快照（009 M1；M2/M3 共享的预算账本）。

    Attributes:
        effective_window: 当前 model 的有效输入窗口（= ``LanguageModel.context_window``）。
        last_input_tokens: 上一轮真实 ``usage.prompt_tokens`` 观测值；不能单独作为
            可复用锚点。请求身份验证由 RuntimeContext.request_estimator 负责。
        output_reserve: 给本轮输出预留的 token（按窗口比例算）。
        buffer: 估算误差缓冲（按窗口比例算）。
    """

    effective_window: int
    last_input_tokens: int | None
    output_reserve: int
    buffer: int
    emergency_ratio: float = 1.0

    @property
    def trigger_threshold(self) -> int:
        """触发裁剪 / 压缩的 token 阈值（窗口 − 输出预留 − 缓冲，下限 0）。"""
        base = max(0, self.effective_window - self.output_reserve - self.buffer)
        return int(base * self.emergency_ratio)


@dataclass(frozen=True)
class PriorSummary:
    """已落盘的最近一次折叠点（009 M3）。

    由 ``Conversation`` 从 :meth:`Session.latest_compaction` 派生，放进
    :class:`RuntimeContext`，供 ``SummarizingContextManager`` 平时折叠展示。

    Attributes:
        summary: 结构化摘要文本。
        covered_through_uuid: scope 所指定身份域中，摘要覆盖到的最后一条消息 uuid（含）。
    """

    summary: str
    covered_through_uuid: str
    checkpoint_version: int = 1
    summary_prompt_version: int = 0
    summary_source_from_uuid: str = ""
    retained_from_uuid: str = ""
    omitted_before_count: int = 0
    scope: Literal["business", "model"] = "business"
    summary_message_uuid: str = ""
    checkpoint_id: str = ""
    materialized: bool = False
    business_covered_through_uuid: str = ""
    task_anchor_uuid: str = ""
    source_user_event_uuid: str = ""
    source_user_sha256: str = ""
    task_anchor_sha256: str = ""
    task_anchor_version: int = 0
    task_anchor: Message | None = None


@dataclass(frozen=True)
class CompactionRecord:
    """本轮新生成、待 ``Conversation`` 落盘为 ``compaction`` 事件的摘要记录（009 M3）。

    context manager 只负责"生成"（含调 LLM），落盘 IO 由 ``Conversation`` 读
    :attr:`BuildResult.new_compaction` 后执行——保持 context manager 无存储副作用。

    Attributes:
        summary: 结构化摘要文本。
        covered_through_uuid: scope 所指定身份域中，被覆盖的最后一条消息 uuid（含）。
        tokens_before: 压缩前的 token 估算（observability）。
        tokens_after: 压缩后的 token 估算（observability）。
        model: 生成本摘要所用的 model 名。
    """

    summary: str
    covered_through_uuid: str
    tokens_before: int = 0
    tokens_after: int = 0
    model: str = ""
    checkpoint_version: int = 2
    summary_prompt_version: int = 1
    summary_source_from_uuid: str = ""
    retained_from_uuid: str = ""
    omitted_before_count: int = 0
    effective_window: int = 0
    calibration_factor: float = 1.0
    trigger_reason: str = "budget"
    summary_attempts: int = 1
    tool_results_truncated: int = 0
    tool_results_omitted: int = 0
    activity_id: str = ""
    scope: Literal["business", "model"] = "business"
    summary_message_uuid: str = ""
    model_prefix_sha256: str = ""
    business_covered_through_uuid: str = ""
    business_prefix_sha256: str = ""
    task_anchor_uuid: str = ""
    source_user_event_uuid: str = ""
    source_user_sha256: str = ""
    task_anchor_sha256: str = ""
    task_anchor_version: int = 0


@dataclass(frozen=True)
class PriorToolContextEdit:
    """已落盘的最近一次 tool result 模型视图清理点。"""

    cleared_result_uuids: tuple[str, ...]
    policy_version: int = 1


@dataclass(frozen=True)
class ToolContextEditRecord:
    """待落盘为 ``tool_context_edit`` 事件的低水位清理候选。"""

    cleared_result_uuids: tuple[str, ...]
    newly_cleared_result_uuids: tuple[str, ...]
    tokens_before: int
    tokens_after: int
    cleared_tokens: int
    high_water_tokens: int
    low_water_tokens: int
    policy_version: int = 1


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
    llm_client: LanguageModel
    model_id: str = ""
    prior_summary: PriorSummary | None = None
    prior_tool_context_edit: PriorToolContextEdit | None = None
    request_overhead_tokens: int = 0
    tool_result_recovery_available: bool = False
    calibration_factor: float = 1.0
    trigger_reason: str = "budget"
    run_id: str | None = None
    request_estimator: Callable[[list[Message]], int] | None = None
    request_assessment: Callable[[list[Message]], InputEstimate] | None = None
    compaction_control: CompactionControl | None = None
    session_id: str | None = None
    surface: str | None = None
    cancel_check: Callable[[], None] | None = None

    def check_cancelled(self) -> None:
        if self.cancel_check is not None:
            self.cancel_check()


@dataclass
class BuildResult:
    """:meth:`ContextManager.build_messages` 的结构化返回值。

    Attributes:
        messages: 实际要发给 LLM 的消息序列（``system`` 在最前；当前回合的
            ``pre_user_context`` 位于 ``history`` 与 ``new_user_input`` 之间，
            ``turn_context`` 位于 ``new_user_input`` 之后；若有
            ``trailing_system`` 则在最末）。
        dropped_count: 截掉的历史消息条数。:class:`NaiveContextManager` 永远是 0。
        new_compaction: 本轮新生成、待 ``Conversation`` 落盘的摘要记录（009 M3）；
            未触发压缩时为 ``None``。
        new_tool_context_edit: 本轮新生成、待 ``Conversation`` 落盘的 tool result
            清理记录；未触发时为 ``None``。
        notes: 扩展用 observability 字段（如 ``token_estimate`` / ``trigger_threshold``
            / ``compacted`` / ``fell_back``）。
    """

    messages: list[Message]
    dropped_count: int = 0
    new_compaction: CompactionRecord | None = None
    new_tool_context_edit: ToolContextEditRecord | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextCompactionProgress:
    """一次上下文压缩在构建阶段产生的内部实时进度。"""

    activity_id: str
    phase: Literal["started", "discarded"]


class ContextManager(Protocol):
    """上下文管理器的接口契约。

    所有实现都必须遵循以下不变量（009 起放松，向后兼容）：

    1. ``system_prompt`` 不为空时，必须放在 :attr:`BuildResult.messages` 的最前
    2. ``new_user_input`` **若提供**（非 ``None``），放在 ``new_user_input``/``history``
       序列的最后；续轮 / 兜底收尾传 ``None`` 表示本轮无新用户输入
    3. ``extra_context`` 若提供，应放在 ``system`` 之后、``history`` 之前；只用于
       确实需要成为稳定前缀的上下文，不承载每回合变化的记忆或运行时事实
    4. ``pre_user_context`` 若提供，放在 ``history`` 之后、当前真实 user 之前；用于
       新增的 append-only world state，不得承载本轮用户级指令
    5. ``turn_context`` 若提供，作为 model-only staged tail 放在当前真实 user 之后；
       ContextManager 自身不持久化它。调用方在 provider 接受后可以把实际 build
       纳入进程内 model history，使记忆召回等 item 后续保持原位置；带
       ``model_only`` 的消息不进入持久化摘要正文
    6. ``trailing_user`` 若提供，rendered 为 ``role="user"`` 消息，位置在
       ``turn_context`` 之后、``trailing_system`` 之前。语义：仅活在 LLM 视图、
       **不落盘**——主动 source（``system_trigger``）注入触发信号专用（021）。与
       ``new_user_input``（已落盘的真用户输入）语义边界明确，互不替代
    7. ``trailing_system`` 若提供，放在**所有消息之后**（比 ``turn_context`` 还靠后），
       用于工具调用循环兜底收尾的临时指令
    8. ``history`` 可能被截断 / 折叠，截断的条数记入 :attr:`BuildResult.dropped_count`
    9. ``runtime`` 为 ``None`` 时实现应退化为"不做预算判断"（:class:`NaiveContextManager`
       行为完全不变）；非 ``None`` 时按策略做度量 / 裁剪 / 压缩

    Note:
        ``history`` 是本次冻结的模型视图，可能已包含先前生成的摘要。再次压缩时
        必须保留该摘要中的信息；checkpoint 校验与折叠使用同一消息身份空间。
    """

    def build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        new_user_attachments: list[AttachmentRef] | None = None,
        extra_context: list[Message] | None = None,
        pre_user_context: list[Message] | None = None,
        turn_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> BuildResult: ...


@runtime_checkable
class StreamingContextManager(Protocol):
    """可在同步上下文构建中先交还压缩进度的可选能力。"""

    def stream_build_messages(
        self,
        history: list[Message],
        system_prompt: str,
        new_user_input: str | None = None,
        new_user_attachments: list[AttachmentRef] | None = None,
        extra_context: list[Message] | None = None,
        pre_user_context: list[Message] | None = None,
        turn_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
        runtime: RuntimeContext | None = None,
    ) -> Generator[ContextCompactionProgress, None, BuildResult]: ...


def stream_build_messages(
    manager: ContextManager,
    *,
    history: list[Message],
    system_prompt: str,
    new_user_input: str | None = None,
    new_user_attachments: list[AttachmentRef] | None = None,
    extra_context: list[Message] | None = None,
    pre_user_context: list[Message] | None = None,
    turn_context: list[Message] | None = None,
    trailing_user: str | None = None,
    trailing_system: str | None = None,
    runtime: RuntimeContext | None = None,
) -> Generator[ContextCompactionProgress, None, BuildResult]:
    """统一适配支持进度和仅支持同步返回的 context manager。"""

    if isinstance(manager, StreamingContextManager):
        return (
            yield from manager.stream_build_messages(
                history=history,
                system_prompt=system_prompt,
                new_user_input=new_user_input,
                new_user_attachments=new_user_attachments,
                extra_context=extra_context,
                pre_user_context=pre_user_context,
                turn_context=turn_context,
                trailing_user=trailing_user,
                trailing_system=trailing_system,
                runtime=runtime,
            )
        )
    return manager.build_messages(
        history=history,
        system_prompt=system_prompt,
        new_user_input=new_user_input,
        new_user_attachments=new_user_attachments,
        extra_context=extra_context,
        pre_user_context=pre_user_context,
        turn_context=turn_context,
        trailing_user=trailing_user,
        trailing_system=trailing_system,
        runtime=runtime,
    )


def assemble_messages(
    history: list[Message],
    system_prompt: str,
    new_user_input: str | None = None,
    new_user_attachments: list[AttachmentRef] | None = None,
    extra_context: list[Message] | None = None,
    pre_user_context: list[Message] | None = None,
    turn_context: list[Message] | None = None,
    trailing_user: str | None = None,
    trailing_system: str | None = None,
) -> list[Message]:
    """按 :class:`ContextManager` 不变量把各成分拼成消息序列（所有策略共用）。

    顺序：``[system?] + extra_context? + history + pre_user_context? + [new_user_input?]
    + turn_context? + [trailing_user?] + [trailing_system?]``。
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
    if pre_user_context:
        messages.extend(pre_user_context)
    if new_user_input is not None:
        messages.append(
            Message(
                role="user",
                content=new_user_input,
                attachments=list(new_user_attachments or []),
                meta={"current_input": True},
            )
        )
    if turn_context:
        messages.extend(turn_context)
    if trailing_user is not None:
        messages.append(Message(role="user", content=trailing_user))
    if trailing_system is not None:
        messages.append(Message(role="system", content=trailing_system))
    return messages
