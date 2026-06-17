"""``Conversation``：绑定 :class:`Session` 跑对话的运行时执行器。

负责把 :class:`PromptBuilder` / :class:`ContextManager` / :class:`LLMClient` /
:class:`Memory` 串起来，并在每轮对话/切换时把事件 append 到 :class:`Session`
和 :class:`SessionStore`：

::

    PromptBuilder.build()  →  system_prompt
                                    ↓
    Memory.retrieve(user)  →  extra_context (可选)
                                    ↓
    ContextManager.build_messages(session.messages, system, user, extra)
                                    ↓
    LLMClient.complete(...) / .stream(...)
                                    ↓
    Event(user_message)  +  Event(assistant_message)
                                    ↓
    session.append + store.append_event

详见 docs/requirements/002-engine-session-management/design.md §4.7。

Note:
    本类**不持久化的逻辑**——所有 IO 都委托给注入的 :class:`SessionStore`。
    历史从 :attr:`Session.messages` 派生，没有内部 ``_history`` 副本。
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from llm_providers import (
    LLMClient,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)
from memory import ConversationFragment, Memory, MemoryContext, Utterance

from .context import (
    BuildResult,
    CompactionRecord,
    ContextManager,
    PriorSummary,
    RuntimeContext,
    make_budget_snapshot,
)
from .conversation_events import (
    ConversationEvent,
    TextDelta,
    ToolCallRequest,
    ToolCallResult,
    TurnDone,
)
from .errors import AgentError
from .messages import Message
from .personas import PersonaCatalog
from .prompts import PromptBuilder
from .sessions import Event, Session, SessionStore
from .tools import ToolRegistry, ToolResult

if TYPE_CHECKING:
    LLMClientFactory = Callable[[str], LLMClient]
    PromptBuilderFactory = Callable[[str], PromptBuilder]


# 014: tool-hook 注入点。AgentRuntime 装配时传入；None 时 _invoke_tool_safely 走原行为。
ToolHookInvoker = Callable[[str, dict[str, Any], Callable[[], ToolResult]], ToolResult]


MAX_TOOL_TURNS_DEFAULT = 5
"""工具调用循环的默认硬上限。可被环境变量 ``AGENT_MAX_TOOL_TURNS`` 覆盖。

经验值：单次 turn 通常 1 ~ 2 次 tool 调用足够；3 次以上往往是 LLM 在死循环。
触上限时由 :meth:`Conversation._finalize_on_tool_loop_limit` 注入收尾 system msg
引导 LLM 用已有信息直接回复用户。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.6.3。
"""


def _resolve_max_tool_turns() -> int:
    """读 ``AGENT_MAX_TOOL_TURNS`` 环境变量，无效或缺失时回落到默认值。"""
    raw = os.environ.get("AGENT_MAX_TOOL_TURNS")
    if not raw:
        return MAX_TOOL_TURNS_DEFAULT
    try:
        v = int(raw)
        return v if v > 0 else MAX_TOOL_TURNS_DEFAULT
    except ValueError:
        return MAX_TOOL_TURNS_DEFAULT


class Conversation:
    """绑定 :class:`Session` 的对话执行器。

    Args:
        session: 已加载或新建的会话。历史从 ``session.events`` 派生。
        store: 持久化实现，每次 send / stream / switch_* 都会写盘。
        llm_client: 当前激活 model 对应的 :class:`LLMClient`。
        context_manager: 上下文管理策略，如 :class:`agent.NaiveContextManager`。
        prompt_builder: 当前激活 persona 对应的 :class:`PromptBuilder`。
            **构造时立刻 build 一次**并缓存；切换 persona 时会通过工厂重建。
        llm_client_factory: 接 model 名 → :class:`LLMClient`，用于 :meth:`switch_model`。
            未注入时调 :meth:`switch_model` 会抛 :class:`AgentError`。
        prompt_builder_factory: 接 **persona_id** → :class:`PromptBuilder`，用于
            :meth:`switch_persona`。未注入时调 :meth:`switch_persona` 会抛 :class:`AgentError`。
        catalog: 用于 ``switch_persona`` 时通过 id 反查 name 写双字段事件 hint。
            ``None`` 时新建一个默认的（孵化期接受）。
        memory: 可选的 :class:`memory.Memory` 实现，阶段 1 不传（占位）。

    Note:
        典型构造方式是用 :meth:`SessionManager.start_conversation` 装配，
        手工构造仅用于测试。
    """

    def __init__(
        self,
        session: Session,
        store: SessionStore,
        llm_client: LLMClient,
        context_manager: ContextManager,
        prompt_builder: PromptBuilder,
        llm_client_factory: LLMClientFactory | None = None,
        prompt_builder_factory: PromptBuilderFactory | None = None,
        catalog: PersonaCatalog | None = None,
        memory: Memory | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_hook_invoker: ToolHookInvoker | None = None,
        post_turn_external: bool = False,
    ):
        self._session = session
        self._store = store
        self._llm_client = llm_client
        self._context_manager = context_manager
        self._prompt_builder = prompt_builder
        self._llm_client_factory = llm_client_factory
        self._prompt_builder_factory = prompt_builder_factory
        self._catalog = catalog or PersonaCatalog()
        self._memory = memory
        self._tool_registry = tool_registry
        # 014: tool hook 与 PostTurn 外接（由 AgentRuntime 装配时打开）
        self._tool_hook_invoker = tool_hook_invoker
        self._post_turn_external = post_turn_external
        self._max_tool_turns = _resolve_max_tool_turns()
        self._last_memory_context: MemoryContext | None = None
        # 009 M1：上下文管理的会话级运行时状态
        # ``_last_input_tokens``：上轮真实 usage.prompt_tokens 锚点（消费 LLMTurnDone.usage
        # 更新）；``_last_context_notes``：最近一轮上下文组装的度量观测（token 估算 /
        # 有效窗口 / 触发阈值），供调试入口读取（AC-1.3）。
        self._last_input_tokens: int | None = None
        self._last_context_notes: dict[str, Any] = {}
        # 007 起 ``_system_prompt`` 不再缓存——每次调用 :attr:`_current_system_prompt`
        # 让 :class:`agent.system_prompt.ChannelSection` 等动态 section 在 channel
        # 切换后立刻生效。``__init__`` 时仍 build 一次做客户端预检（persona 不存在
        # 等错误尽早暴露）。
        prompt_builder.build()

    # ----- 只读视图 -----

    @property
    def session(self) -> Session:
        """绑定的会话实例（只读引用，请勿直接修改 events）。"""
        return self._session

    @property
    def history(self) -> list[Message]:
        """对话历史的只读视图，从 :attr:`Session.messages` 派生。

        每次访问都会遍历事件流（O(n)），孵化期 n < 1000，无性能问题。
        """
        return self._session.messages

    @property
    def _system_prompt(self) -> str:
        """每轮重建 system_prompt（007 起；让 ChannelSection 等动态 section 生效）。

        本属性内部封装 :meth:`PromptBuilder.build`；调用频率为每轮 send / stream
        各 1~2 次（首轮 + 工具循环续轮），开销主要是 persona body 文件读 + 默认
        section 资源读，单次 < 1ms，孵化期可接受。

        switch_persona 时已重建 :attr:`_prompt_builder`，本属性会自动反映新 persona。
        """
        return self._prompt_builder.build()

    @property
    def current_persona(self) -> str:
        """当前激活 persona 名（从 session 派生）。"""
        return self._session.current_persona

    @property
    def current_model(self) -> str:
        """当前激活 model 名（从 session 派生）。"""
        return self._session.current_model

    @property
    def last_memory_context(self) -> MemoryContext | None:
        """最近一轮 :meth:`memory.Memory.retrieve` 的结果（observability / 调试）。

        无 memory 或尚未对话时为 ``None``；空召回时是 ``MemoryContext.empty()``。
        """
        return self._last_memory_context

    @property
    def last_context_notes(self) -> dict[str, Any]:
        """最近一轮上下文组装的度量观测（009 M1；observability / 调试，AC-1.3）。

        每次发往 LLM 的上下文组装后刷新，含：

        - ``token_estimate`` — 本轮组装消息的保守 token 估算
        - ``effective_window`` — 当前 model 的有效输入窗口
        - ``trigger_threshold`` — 按窗口动态推导的触发阈值
        - ``last_input_tokens`` — 上轮真实 usage 锚点（无则 ``None``）
        - ``dropped_count`` — 本轮裁剪条数（Naive 恒 0）
        - 以及 context manager 经 ``BuildResult.notes`` 透出的其它字段（M2/M3）

        尚未发生任何组装时为空 dict。
        """
        return dict(self._last_context_notes)

    # ----- 对话主流程 -----

    def send(self, user_input: str) -> str:
        """同步发送一轮对话，返回完整回复（含可能的工具调用循环结果）。

        本方法**内部消费 :meth:`stream`**，把所有 :class:`TextDelta` 拼起来
        作为最终回复——这意味着如果 AI 决定调工具，``send()`` 会等工具执行
        完毕、AI 整合工具结果生成最终文本后才返回。调用方拿不到工具调用的
        中间过程信息（按 ``send`` 这个方法名所代表的"问一句答一句"语义）。

        Args:
            user_input: 用户输入文本。

        Returns:
            AI 整合所有工具结果后的完整回复字符串。
            如果工具调用失败，返回的是 AI 拟人化兜底后的文案（不暴露技术错误）。

        Raises:
            llm_providers.LLMError: 任意子类。落盘语义同 :meth:`stream`。
            SessionPersistError: 事件落盘失败。

        Note:
            **如需观测工具调用过程**（CLI / 调试），改用 :meth:`stream`。
        """
        text_buf: list[str] = []
        for ev in self.stream(user_input):
            if isinstance(ev, TextDelta):
                text_buf.append(ev.text)
        return "".join(text_buf)

    def stream(self, user_input: str) -> Iterator[ConversationEvent]:
        """流式发送一轮对话，按 :class:`ConversationEvent` discriminated union
        yield 多通道事件（文本增量 / 工具调用请求 / 工具调用结果 / 完成）。

        典型用法（按事件类型分派渲染）::

            for ev in conv.stream(user_input):
                if isinstance(ev, TextDelta):
                    print(ev.text, end="", flush=True)
                elif isinstance(ev, ToolCallRequest):
                    log_tool_call(ev)
                elif isinstance(ev, ToolCallResult):
                    log_tool_result(ev)
                elif isinstance(ev, TurnDone):
                    print()  # 换行

        工具调用循环骨架：

        1. 第一轮调 LLM（带 ``tools`` 参数若 ``tool_registry`` 非空）
        2. 累积文本和 tool_calls；按 ``stop_reason`` 分派：

           - ``end_turn`` 或无 tool_calls → 落盘 assistant_event，yield ``TurnDone``，结束
           - ``tool_use`` → 落盘 assistant_event + tool_call_request_events，
             顺序执行所有 tool（捕获异常转 :class:`ToolResult` ``is_error=True``），
             落盘 tool_call_result_events，进入下一轮
        3. 触 :data:`MAX_TOOL_TURNS_DEFAULT` 上限 → 走
           :meth:`_finalize_on_tool_loop_limit` 兜底，注入收尾 system msg

        Args:
            user_input: 用户输入文本。

        Yields:
            :class:`TextDelta` / :class:`ToolCallRequest` / :class:`ToolCallResult` /
            :class:`TurnDone`。

        Raises:
            llm_providers.LLMError: 任意子类。**第一轮初始化阶段抛**（如认证错）
                则事件完全不落盘；**迭代中途抛 / 后续轮抛** 则会落本轮已经累积
                的部分内容为 ``partial=True`` 的 assistant_message
                （已成功完成的前几轮 tool 调用都已落盘，不受影响）。
            SessionPersistError: 事件落盘失败。

        Note:
            ``KeyboardInterrupt`` 等 :class:`BaseException` 会被 ``finally``
            通过 :func:`sys.exc_info` 识别为中断，按"部分回复"语义落盘。
        """
        persona_name = self._session.current_persona_name
        persona_id = self._session.current_persona_id
        model_snapshot = self._session.current_model

        user_event_appended = False
        current_text_buf: list[str] = []
        current_tc_acc: dict[int, dict[str, Any]] = {}
        total_tool_calls = 0
        turn_start_idx = len(self._session.events)

        try:
            for turn_idx in range(self._max_tool_turns + 1):
                current_text_buf = []
                current_tc_acc = {}

                if turn_idx == 0:
                    openai_messages = self._build_openai_messages_first_turn(user_input)
                else:
                    openai_messages = self._build_openai_messages_continuation()

                tool_specs = self._tool_registry.to_openai_tools() if self._tool_registry else None

                stop_reason = ""
                for ev in self._llm_client.stream(openai_messages, tools=tool_specs):
                    if isinstance(ev, LLMTextDelta):
                        current_text_buf.append(ev.text)
                        yield TextDelta(text=ev.text)
                    elif isinstance(ev, LLMToolCallDelta):
                        self._accumulate_tool_call_delta(current_tc_acc, ev)
                    elif isinstance(ev, LLMTurnDone):
                        stop_reason = ev.stop_reason
                        self._consume_usage(ev)

                # 第一轮 LLM 完成才落 user_event（保留"初始化失败完全不落盘"语义）
                if not user_event_appended:
                    self._append_user_event(user_input)
                    user_event_appended = True

                tool_calls = self._finalize_tool_calls(current_tc_acc)

                self._append_assistant_event(
                    "".join(current_text_buf),
                    partial=False,
                    persona_name=persona_name,
                    persona_id=persona_id,
                    model=model_snapshot,
                )

                if not tool_calls or stop_reason != "tool_use":
                    yield TurnDone(stop_reason="end_turn", total_tool_calls=total_tool_calls)
                    return

                # 触上限后不再执行 tool（已经做完了 _max_tool_turns 轮）
                if turn_idx >= self._max_tool_turns:
                    break

                # 执行本轮的所有 tool_calls（顺序）
                for tc in tool_calls:
                    self._append_tool_call_request_event(tc)
                    yield ToolCallRequest(
                        tool_call_id=tc["id"],
                        tool_name=tc["name"],
                        args=tc["args"],
                    )

                    start = time.monotonic()
                    result = self._invoke_tool_safely(tc["name"], tc["args"])
                    duration = time.monotonic() - start

                    self._append_tool_call_result_event(tc, result, duration)
                    yield ToolCallResult(
                        tool_call_id=tc["id"],
                        tool_name=tc["name"],
                        text=result.text,
                        is_error=result.is_error,
                        duration_seconds=duration,
                    )
                    total_tool_calls += 1

            # 走出 for 循环 = 触上限：执行兜底逻辑
            yield from self._finalize_on_tool_loop_limit(
                persona_name=persona_name,
                persona_id=persona_id,
                model_snapshot=model_snapshot,
                total_tool_calls=total_tool_calls,
            )
        finally:
            exc_info = sys.exc_info()
            interrupted = exc_info[0] is not None
            if interrupted:
                self._on_interrupt(
                    user_input=user_input,
                    user_event_appended=user_event_appended,
                    current_text_buf=current_text_buf,
                    persona_name=persona_name,
                    persona_id=persona_id,
                    model_snapshot=model_snapshot,
                )
            else:
                if not self._post_turn_external:
                    # 014: 默认行为保留——AgentRuntime 装配时 post_turn_external=True，
                    # 由 PostTurn hook 调 memory.observe；此处不重复触发
                    self._observe_turn(turn_start_idx)

    # ----- 系统级触发轮（014：main loop 入口） -----

    def dispatch_system_turn(
        self,
        *,
        source_kind: str,
        system_prompt_addendum: str,
        output_visibility: Literal["user", "memory_only"] = "user",
    ) -> Iterator[ConversationEvent]:
        """系统级触发轮入口（014 R-4.4）。

        外部（:class:`agent.runtime.AgentRuntime`）按 ``SystemTriggerEvent`` 转译
        后调本方法，让"非 user 事件"翻译成 conversation 能消费的输入——无需伪装成
        ``user_message``。

        Args:
            source_kind: 触发源 kind，落入 ``system_trigger.payload.source_kind``
                （如 ``"cron:bedtime"`` / ``"idle_reflection"``）。
            system_prompt_addendum: 追加到 system message 末尾的引导话
                （如"现在是约定的休息时间……"）。
            output_visibility: ``"user"`` = 与 :meth:`stream` 同形 yield 事件，
                上游订阅者（bridge push subscriber）能看到；``"memory_only"`` =
                silent turn，**不 yield**任何 ConversationEvent；LLM 输出文本写
                ``memory_observation`` 事件并自构 :class:`memory.ConversationFragment`
                喂 :meth:`memory.Memory.observe`，``session.messages`` 派生不含
                此文本（历史天然干净）。

        Yields:
            ``output_visibility="user"`` 时：:class:`TextDelta` 增量 +
            :class:`TurnDone`；``output_visibility="memory_only"`` 时：什么都不 yield。

        Note:
            本期 silent / system 触发轮**不开 tool**（``tools=None``），避免意外
            副作用 / 长耗时——主动陪伴场景的 tool 调用留下个需求扩展。
        """
        persona_name = self._session.current_persona_name
        persona_id = self._session.current_persona_id
        model_snapshot = self._session.current_model

        # 1. 落 system_trigger marker（与 compaction 同模式，不参与 messages 派生）
        self._append_system_trigger_event(
            source_kind=source_kind,
            system_prompt_addendum=system_prompt_addendum,
            output_visibility=output_visibility,
        )

        # 2. 跑 LLM stream（注入引导话作 trailing user，021：role=user 才是 turn 切换信号）
        openai_messages = self._assemble(trailing_user=system_prompt_addendum)
        text_buf: list[str] = []
        for ev in self._llm_client.stream(openai_messages, tools=None):
            if isinstance(ev, LLMTextDelta):
                text_buf.append(ev.text)
                if output_visibility == "user":
                    yield TextDelta(text=ev.text)
            elif isinstance(ev, LLMTurnDone):
                self._consume_usage(ev)
            # silent / system 触发轮不开 tool，不处理 LLMToolCallDelta

        full_text = "".join(text_buf)

        # 3. 按 visibility 分支落事件
        if output_visibility == "user":
            self._append_assistant_event(
                full_text,
                partial=False,
                persona_name=persona_name,
                persona_id=persona_id,
                model=model_snapshot,
            )
            yield TurnDone(stop_reason="end_turn", total_tool_calls=0)
            return

        # output_visibility == "memory_only"：silent turn
        # - 落 memory_observation event（marker，不参与 messages 派生）
        # - 自构 ConversationFragment 喂 memory.observe（speaker="agent"，1 utterance）
        # - 不 yield TurnDone，对上游完全不可见
        obs_uuid = str(uuid4())
        now = datetime.now(UTC)
        self._append_memory_observation_event(
            uuid=obs_uuid,
            ts=now,
            text=full_text,
            source_kind=source_kind,
            persona_id=persona_id or "",
        )
        if self._memory is not None and full_text:
            fragment = ConversationFragment(
                session_id=self._session.session_id,
                utterances=[
                    Utterance(
                        speaker="agent",
                        text=full_text,
                        ts=now,
                        source_ref=f"{self._session.session_id}#{obs_uuid}",
                    )
                ],
                persona_id=persona_id or "",
            )
            try:
                self._memory.observe(fragment)
            except Exception:
                import logging

                logging.getLogger(__name__).warning("silent turn observe 失败", exc_info=True)

    # ----- 切换 persona / model -----

    def switch_persona(self, persona_id: str) -> None:
        """切换 persona：落盘 ``persona_change`` 事件（双字段 id+name）+ 重建 PromptBuilder。

        若新 persona 与当前相同则**无操作直接返回**（不写事件）。

        Args:
            persona_id: 目标 persona 的 UUID。

        Raises:
            AgentError: ``prompt_builder_factory`` 未注入。
            PersonaNotFoundError: 该 id 在 catalog 找不到。**此时事件未落盘**，
                状态完全不变。
            SessionPersistError: 事件落盘失败。**此时 PromptBuilder 已构造但未替换**，
                状态完全不变。

        Note:
            实施顺序：``catalog.get(id)`` 取 name → ``factory(id)`` + 预先 ``build()``
            （客户端预检）→ ``store.append_event`` → ``session.append`` → swap 内部依赖。
        """
        if self._prompt_builder_factory is None:
            raise AgentError(
                "switch_persona 需要 prompt_builder_factory 注入（请通过 SessionManager 装配）"
            )

        from_id = self._session.current_persona_id
        from_name = self._session.current_persona_name
        if from_id == persona_id:
            return

        new_info = self._catalog.get(persona_id)
        new_builder = self._prompt_builder_factory(persona_id)
        new_builder.build()  # 客户端预检：persona body 加载失败应在落事件前抛出

        event = Event(
            type="persona_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "from_id": from_id,
                "from": from_name,
                "to_id": persona_id,
                "to": new_info.name,
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

        self._prompt_builder = new_builder

    def switch_model(self, name: str) -> None:
        """切换 model：落盘 ``model_change`` 事件 + 重建 :class:`LLMClient`。

        若新 model 与当前相同则**无操作直接返回**。

        Args:
            name: 目标 model 名（LiteLLM 风格，如 ``"deepseek/deepseek-v4-flash"``）。

        Raises:
            AgentError: ``llm_client_factory`` 未注入。
            LLMAuthError / ValueError: 工厂构造 LLMClient 失败。**事件未落盘**，状态不变。
            SessionPersistError: 事件落盘失败。**LLMClient 已构造但未替换**，状态不变。
        """
        if self._llm_client_factory is None:
            raise AgentError(
                "switch_model 需要 llm_client_factory 注入（请通过 SessionManager 装配）"
            )

        from_model = self._session.current_model
        if from_model == name:
            return

        new_client = self._llm_client_factory(name)

        event = Event(
            type="model_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"from": from_model, "to": name},
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

        self._llm_client = new_client

    def switch_channel(self, to: str) -> None:
        """切换 channel：落盘 ``channel_change`` 事件（007 起新增）。

        若新 channel 与当前相同则**无操作直接返回**（幂等）。

        与 :meth:`switch_persona` / :meth:`switch_model` 不同，channel 切换不
        重建 LLMClient 也不重建 PromptBuilder——通道差异化通过
        :class:`agent.system_prompt.ChannelSection` 在每轮 :class:`SystemPromptComposer`
        compose 时根据 ``session.current_channel`` 动态生效，无需 conversation
        持有的依赖动手术。

        Args:
            to: 目标 channel：``"voice"`` 或 ``"text"``。

        Raises:
            ValueError: ``to`` 不在 ``("voice", "text")`` 中。
            SessionPersistError: 事件落盘失败。
        """
        if to not in ("voice", "text"):
            raise ValueError(f"channel 必须是 voice / text，实际: {to!r}")

        from_channel = self._session.current_channel
        if from_channel == to:
            return

        event = Event(
            type="channel_change",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"from": from_channel, "to": to},
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    # ----- 内部辅助 -----

    def _append_user_event(self, content: str) -> None:
        event = Event(
            type="user_message",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"content": content},
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _append_assistant_event(
        self,
        content: str,
        *,
        partial: bool,
        persona_name: str,
        persona_id: str | None,
        model: str,
    ) -> None:
        meta: dict[str, Any] = {"persona": persona_name, "model": model}
        if persona_id is not None:
            meta["persona_id"] = persona_id
        event = Event(
            type="assistant_message",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={"content": content, "partial": partial},
            meta=meta,
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _build_openai_messages_first_turn(self, user_input: str) -> list[dict[str, Any]]:
        """工具调用循环第 0 轮：通过统一入口 :meth:`_assemble` 把 ``user_input``
        作为最新一条 user 消息组装。

        若注入了 memory，先 ``retrieve`` 召回相关记忆，渲染成单条 system 消息走
        ``extra_context`` 注入（008 design §6.1）。空召回不注入任何记忆段。
        """
        extra: list[Message] | None = None
        if self._memory is not None:
            ctx = self._memory.retrieve(
                user_input,
                persona_id=self._session.current_persona_id or "",
                session_id=self._session.session_id,
            )
            self._last_memory_context = ctx
            if not ctx.is_empty():
                extra = [Message(role="system", content=ctx.rendered)]
        return self._assemble(new_user_input=user_input, extra_context=extra)

    def _assemble(
        self,
        *,
        new_user_input: str | None = None,
        extra_context: list[Message] | None = None,
        trailing_user: str | None = None,
        trailing_system: str | None = None,
    ) -> list[dict[str, Any]]:
        """统一所有发往 LLM 的上下文组装入口（009 R-0.3）。

        首轮 / 工具续轮 / 触上限兜底收尾都经此处，确保上下文管理（度量 /
        兜底截断 / 摘要压缩）覆盖每一次组装，差异只在 ``new_user_input`` /
        ``extra_context`` / ``trailing_user`` / ``trailing_system``。``history``
        一律传**原始全量** :attr:`Session.messages`（折叠在 context manager 内部
        发生，见 009 design §6.3）。

        ``trailing_user``（021 新增）：主动 source 注入 ``role="user"`` 触发信号
        用，与 ``trailing_system`` 互不替代——前者是 turn 切换信号、后者是兜底
        收尾指令。详见 021 design §5。

        Returns:
            可直接喂给 :meth:`LLMClient.stream` / ``complete`` 的 OpenAI 风格 dict 列表。
        """
        runtime = self._build_runtime_context()
        build = self._context_manager.build_messages(
            history=self._session.messages,
            system_prompt=self._system_prompt,
            new_user_input=new_user_input,
            extra_context=extra_context,
            trailing_user=trailing_user,
            trailing_system=trailing_system,
            runtime=runtime,
        )
        # 009 M3：context manager 只"生成"摘要，落盘 IO 在此执行（职责边界，design §4.3）。
        if build.new_compaction is not None:
            self._append_compaction_event(build.new_compaction)
        self._record_context_notes(build, runtime)
        return [m.to_openai() for m in build.messages]

    def _build_runtime_context(self) -> RuntimeContext:
        """构造本轮 :class:`RuntimeContext`（预算快照 + 当前 llm_client）。

        ``llm_client`` per-call 传入 → ``switch_model`` 后自动跟随新客户端 / 新窗口。
        ``prior_summary`` 从 :meth:`Session.latest_compaction` 派生（M3 起）：有最近
        折叠点则带上 summary + 覆盖范围，``SummarizingContextManager`` 据此折叠展示；
        老会话 / 未压缩过为 ``None``。
        """
        budget = make_budget_snapshot(
            effective_window=self._llm_client.context_window,
            last_input_tokens=self._last_input_tokens,
        )
        return RuntimeContext(
            budget=budget,
            llm_client=self._llm_client,
            prior_summary=self._derive_prior_summary(),
        )

    def _derive_prior_summary(self) -> PriorSummary | None:
        """从最近一条 ``compaction`` 事件派生 :class:`PriorSummary`（无则 ``None``）。"""
        comp = self._session.latest_compaction()
        if comp is None:
            return None
        summary = comp.payload.get("summary", "")
        covered = comp.payload.get("covered_through_uuid", "")
        if not summary or not covered:
            return None
        return PriorSummary(summary=summary, covered_through_uuid=covered)

    def _append_compaction_event(self, record: CompactionRecord) -> None:
        """把本轮新生成的摘要落为 ``compaction`` 事件（append-only，不动原始消息）。

        原始 user/assistant/tool 事件一条不删不改；compaction 只是流上叠加的折叠点
        marker。下一轮 :meth:`_derive_prior_summary` 会读到它做折叠（009 design §6.4）。
        """
        event = Event(
            type="compaction",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "summary": record.summary,
                "covered_through_uuid": record.covered_through_uuid,
                "tokens_before": record.tokens_before,
                "tokens_after": record.tokens_after,
                "model": record.model,
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _record_context_notes(self, build: BuildResult, runtime: RuntimeContext) -> None:
        """把本轮组装的度量观测刷进 :attr:`last_context_notes`（AC-1.3）。"""
        from .context import estimate_tokens

        notes: dict[str, Any] = {
            "token_estimate": estimate_tokens(build.messages),
            "effective_window": runtime.budget.effective_window,
            "trigger_threshold": runtime.budget.trigger_threshold,
            "last_input_tokens": runtime.budget.last_input_tokens,
            "dropped_count": build.dropped_count,
        }
        notes.update(build.notes)
        self._last_context_notes = notes

    def _consume_usage(self, ev: LLMTurnDone) -> None:
        """消费 :class:`LLMTurnDone.usage`，更新下一轮估算的真实锚点（009 M1）。

        provider 不透出 usage（``None``）时保留上次锚点，下轮估算退化到纯字符。
        """
        if ev.usage is not None and ev.usage.prompt_tokens > 0:
            self._last_input_tokens = ev.usage.prompt_tokens

    def _observe_turn(self, start_idx: int) -> None:
        """本轮成功结束后，把这一轮新增的事件投影成 fragment 交给 memory 抽取。

        memory 未注入则跳过。投影 / 入队都不应影响对话主流程，出错只吞掉
        （抽取是旁路；observe 本身是非阻塞入队）。
        """
        if self._memory is None:
            return
        from .memory_feed import project_turn

        try:
            new_events = self._session.events[start_idx:]
            fragment = project_turn(
                new_events,
                session_id=self._session.session_id,
                persona_id=self._session.current_persona_id or "",
            )
            self._memory.observe(fragment)
        except Exception:
            import logging

            logging.getLogger(__name__).warning("observe 本轮对话失败", exc_info=True)

    def _build_openai_messages_continuation(self) -> list[dict[str, Any]]:
        """工具调用循环第 N>0 轮：经统一入口 :meth:`_assemble` 组装（009 R-0.3）。

        续轮没有新用户输入（用户输入早进了 :attr:`Session.messages`），故
        ``new_user_input=None``。009 起续轮也经过上下文管理——确保带工具的长会话
        续轮同样受度量 / 兜底 / 压缩保护，不再绕过 :class:`ContextManager`。
        """
        return self._assemble()

    def _accumulate_tool_call_delta(
        self,
        acc: dict[int, dict[str, Any]],
        delta: LLMToolCallDelta,
    ) -> None:
        """把 :class:`LLMToolCallDelta` 增量累积到 ``acc`` 字典里（按 index 分组）。"""
        slot = acc.setdefault(
            delta.index,
            {"id": "", "name": "", "args_json": ""},
        )
        if delta.tool_call_id:
            slot["id"] = delta.tool_call_id
        if delta.tool_name:
            slot["name"] = delta.tool_name
        slot["args_json"] += delta.args_json_delta

    def _finalize_tool_calls(self, acc: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        """把累积好的 tool_call dict 转成最终列表（按 index 顺序）。

        ``args_json`` 字段反序列化为 ``args`` 字典。LLM 偶发吐出
        非法 JSON 时降级为空 dict（让工具自己处理空入参或返回 is_error）。
        """
        import json

        result: list[dict[str, Any]] = []
        for idx in sorted(acc.keys()):
            slot = acc[idx]
            if not slot["id"] or not slot["name"]:
                continue  # 损坏 / 不完整的 tool_call，丢弃
            try:
                args = json.loads(slot["args_json"]) if slot["args_json"] else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result.append({"id": slot["id"], "name": slot["name"], "args": args})
        return result

    def _invoke_tool_safely(self, name: str, args: dict[str, Any]) -> ToolResult:
        """调一次工具，把所有异常转为 :class:`ToolResult` ``is_error=True``。

        014 起：如果构造时注入了 ``tool_hook_invoker``（AgentRuntime 装配时传入），
        实际执行走 invoker（让 PreToolUse / PostToolUse hook 能介入）；否则保留原行为。

        Note:
            :class:`agent.tools.errors.ToolNotFoundError` 也走这里——LLM 调了
            未注册的工具（拼写错误等）应转 error result 喂回 LLM 让它修正后续调用，
            而不是 raise 中断整个循环。
        """
        if self._tool_registry is None:
            return ToolResult(
                text=f"工具 {name!r} 不可用：当前会话未配置工具集",
                is_error=True,
            )

        registry = self._tool_registry

        def _default_invoke() -> ToolResult:
            try:
                return registry.invoke(name, args)
            except Exception as exc:
                return ToolResult(
                    text=f"工具 {name!r} 执行出错: {exc}",
                    is_error=True,
                )

        if self._tool_hook_invoker is not None:
            return self._tool_hook_invoker(name, args, _default_invoke)
        return _default_invoke()

    def _finalize_on_tool_loop_limit(
        self,
        *,
        persona_name: str,
        persona_id: str | None,
        model_snapshot: str,
        total_tool_calls: int,
    ) -> Iterator[ConversationEvent]:
        """工具调用循环触达 :attr:`_max_tool_turns` 上限时的兜底策略。

        注入一条临时 system message，引导 LLM 用已有信息直接回复用户、
        不再调任何工具（``tools=None``）。生成的最终 assistant 文本会落盘。

        **抽出独立方法**便于未来替换不同策略（如改成截断式收尾、
        或交由用户决定继续）。

        详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.6.2。
        """
        extra_system = (
            "你已经多次调用工具仍未达成目标，请基于已有信息用一两句话直接回复用户，"
            "不要再调用任何工具。"
        )
        # 009 R-0.3：兜底收尾也走统一入口，收尾指令作为 trailing_system 注入，
        # 不再手拼消息（消除与续轮组装的重复）。
        openai_messages = self._assemble(trailing_system=extra_system)

        text_buf: list[str] = []
        for ev in self._llm_client.stream(openai_messages, tools=None):
            if isinstance(ev, LLMTextDelta):
                text_buf.append(ev.text)
                yield TextDelta(text=ev.text)
            elif isinstance(ev, LLMTurnDone):
                self._consume_usage(ev)
            # 触上限兜底中不再处理 tool_call_delta（tools=None，理论上 LLM 不该返回）

        self._append_assistant_event(
            "".join(text_buf),
            partial=False,
            persona_name=persona_name,
            persona_id=persona_id,
            model=model_snapshot,
        )
        yield TurnDone(
            stop_reason="max_turns_reached",
            total_tool_calls=total_tool_calls,
        )

    def _on_interrupt(
        self,
        *,
        user_input: str,
        user_event_appended: bool,
        current_text_buf: list[str],
        persona_name: str,
        persona_id: str | None,
        model_snapshot: str,
    ) -> None:
        """被异常 / KeyboardInterrupt 中断时的落盘策略。

        - 第一轮 LLM 还没出任何内容（``not user_event_appended`` 且 buffer 全空）：
          完全不落盘——下次重试干净（与 001 起的语义一致）
        - 否则把当前轮已经累积的部分文本落为 ``partial=True`` 的 assistant_event；
          已成功完成的前几轮 tool 调用事件不受影响
        """
        if not user_event_appended:
            if not current_text_buf:
                return  # 完全没启动，不落盘
            # 罕见：LLM 已经流出文本但中途中断，user_event 还没落
            self._append_user_event(user_input)
        if current_text_buf:
            self._append_assistant_event(
                "".join(current_text_buf),
                partial=True,
                persona_name=persona_name,
                persona_id=persona_id,
                model=model_snapshot,
            )

    def _append_tool_call_request_event(self, tc: dict[str, Any]) -> None:
        """落 ``tool_call_request`` 事件。"""
        event = Event(
            type="tool_call_request",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "tool_call_id": tc["id"],
                "tool_name": tc["name"],
                "args": tc["args"],
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _append_tool_call_result_event(
        self,
        tc: dict[str, Any],
        result: ToolResult,
        duration: float,
    ) -> None:
        """落 ``tool_call_result`` 事件。"""
        meta: dict[str, Any] = {"duration_seconds": duration}
        if result.meta:
            meta["extra"] = dict(result.meta)
        event = Event(
            type="tool_call_result",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "tool_call_id": tc["id"],
                "tool_name": tc["name"],
                "content": result.text,
                "is_error": result.is_error,
            },
            meta=meta,
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _append_system_trigger_event(
        self,
        *,
        source_kind: str,
        system_prompt_addendum: str,
        output_visibility: str,
    ) -> None:
        """落 ``system_trigger`` 事件（014）：non-user 触发轮的 marker。

        与 ``compaction`` 一样不参与 :attr:`Session.messages` 派生——LLM 上下文
        构造看不到，避免污染历史；session 文件回放仍可见，便于审计 / 复盘。
        """
        event = Event(
            type="system_trigger",
            uuid=str(uuid4()),
            ts=datetime.now(UTC),
            payload={
                "source_kind": source_kind,
                "system_prompt_addendum": system_prompt_addendum,
                "output_visibility": output_visibility,
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    def _append_memory_observation_event(
        self,
        *,
        uuid: str,
        ts: datetime,
        text: str,
        source_kind: str,
        persona_id: str,
    ) -> None:
        """落 ``memory_observation`` 事件（014）：silent turn 的产物 marker。

        payload 含 LLM 反思文本，**不进入** :attr:`Session.messages` 派生（与
        ``system_trigger`` / ``compaction`` 同模式）。文件回放可见，便于复盘
        agent 自己对自己说了什么；下次 LLM 上下文构造看不到，避免"用户没听过"
        的内容污染对话历史。
        """
        event = Event(
            type="memory_observation",
            uuid=uuid,
            ts=ts,
            payload={
                "text": text,
                "source_kind": source_kind,
                "persona_id": persona_id,
            },
        )
        self._store.append_event(self._session.session_id, event)
        self._session.append(event)

    # ----- 调试辅助 -----

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Conversation(session_id={self._session.session_id[:8]}, "
            f"persona={self._session.current_persona!r}, "
            f"model={self._session.current_model!r}, "
            f"messages={len(self._session.messages)})"
        )

    # ----- 兼容性显式签名占位（防止误用旧 API）-----

    def reset(self) -> None:
        """**已删除** —— 002 架构下与 ``/new`` 语义重复。

        Raises:
            AttributeError: 请改用 :meth:`SessionManager.create` 新建会话。
        """
        raise AttributeError(
            "Conversation.reset() 已在 002 移除（与 /new 语义重复）。"
            "请改用 SessionManager.create() 新建会话。"
        )

    def dump(self) -> dict[str, Any]:
        """**已删除** —— 持久化走 :class:`SessionStore`。

        Raises:
            AttributeError: 请改用 :meth:`Session.to_dict`。
        """
        raise AttributeError(
            "Conversation.dump() 已在 002 移除。"
            "持久化走 SessionStore；如需快照导出请用 conv.session.to_dict()。"
        )

    @classmethod
    def load(cls, *args: Any, **kwargs: Any) -> Conversation:
        """**已删除** —— 持久化走 :class:`SessionStore`。

        Raises:
            AttributeError: 请改用 :meth:`SessionManager.open` + :meth:`SessionManager.start_conversation`。
        """
        raise AttributeError(
            "Conversation.load() 已在 002 移除。"
            "请改用 SessionManager.open(session_id) + start_conversation(session)。"
        )
