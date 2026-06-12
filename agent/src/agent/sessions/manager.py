"""``SessionManager`` —— 会话业务编排。

所有调用方（CLI / `agent_bridge` HTTP / 未来桌宠前端）通过本类访问会话能力：
**CRUD + 装配 Conversation**。Manager 不直接做文件 IO（委托 :class:`SessionStore`），
不直接调 LLM（委托 :class:`Conversation`）。

:meth:`start_conversation` 通过注入的 ``llm_client_factory`` /
``prompt_builder_factory`` / ``context_manager_factory`` 装配并返回
:class:`~agent.Conversation`。

详见 docs/requirements/002-engine-session-management/design.md §4.4。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..errors import AgentError
from .session import Session
from .store import SessionStore, SessionSummary

if TYPE_CHECKING:
    from llm_providers import LLMClient
    from memory import Memory

    from ..context import ContextManager
    from ..conversation import Conversation, ToolHookInvoker
    from ..prompts import PromptBuilder
    from ..tools import ToolRegistry


TitleGenerator = Callable[[Session], str]
LLMClientFactory = Callable[[str], "LLMClient"]
PromptBuilderFactory = Callable[[str], "PromptBuilder"]
ContextManagerFactory = Callable[[], "ContextManager"]


def _default_title(now: datetime | None = None) -> str:
    """占位标题：``"会话 YYYY-MM-DD HH:MM"``（local time）。"""
    dt = now or datetime.now(UTC).astimezone()
    return f"会话 {dt:%Y-%m-%d %H:%M}"


class SessionManager:
    """会话业务编排。

    Args:
        store: 持久化实现（如 :class:`JsonlSessionStore`）。
        llm_client_factory: 接 model 名 → :class:`LLMClient`。M2.1 阶段可不传，
            M2.2 接 Conversation 后必须注入。
        prompt_builder_factory: 接 persona 名 → :class:`PromptBuilder`。同上。
        context_manager_factory: 接 0 参 → :class:`ContextManager`，**每个会话独立
            产一个实例**（009 起从单例改工厂，与 ``llm_client_factory`` /
            ``prompt_builder_factory`` 邻居一致）。摘要策略需持会话级 circuit breaker
            状态，单例会串话；FIFO / Naive 虽无状态，统一走工厂便于按里程碑切换默认。
            无状态策略可直接传类本身（如 ``context_manager_factory=NaiveContextManager``）。
        title_generator: 可选；若提供，:meth:`create` 在未显式传 ``title`` 时
            会调用它，否则用 :func:`_default_title` 兜底。
        tool_registry: 可选；若提供，:meth:`start_conversation` 装配出的
            :class:`Conversation` 会挂上同一个 registry，让 AI 能调用其中的工具。
            005 起新增。``None``（默认）等价于"不开放任何工具"，行为与 002~004
            完全一致。
        memory: 可选；若提供，:meth:`start_conversation` 装配出的
            :class:`Conversation` 会挂上**同一个** memory 实例（记忆是 user 维度的
            全局单例，跨会话共享）。008 起新增。``None``（默认）等价于"不启用记忆"，
            行为与 002~007 完全一致。
    """

    def __init__(
        self,
        store: SessionStore,
        llm_client_factory: LLMClientFactory | None = None,
        prompt_builder_factory: PromptBuilderFactory | None = None,
        context_manager_factory: ContextManagerFactory | None = None,
        title_generator: TitleGenerator | None = None,
        tool_registry: ToolRegistry | None = None,
        memory: Memory | None = None,
    ) -> None:
        self._store = store
        self._llm_client_factory = llm_client_factory
        self._prompt_builder_factory = prompt_builder_factory
        self._context_manager_factory = context_manager_factory
        self._title_generator = title_generator
        self._tool_registry = tool_registry
        self._memory = memory

    # ----- 会话 CRUD -----

    def create(
        self,
        persona: str,
        model: str,
        title: str | None = None,
        *,
        persona_id: str | None = None,
        session_id: str | None = None,
        channel: str = "text",
    ) -> Session:
        """创建新会话并落盘首行 ``session_meta`` 事件。

        Args:
            persona: 初始 persona 名（slug；显示用）。
            model: 初始 model 名。
            title: 可选；不传则按以下顺序回落：
                ``title_generator(session)`` → :func:`_default_title`。
            persona_id: 初始 persona 的 UUID（003 起的主键）。``None`` 时为兼容
                老调用方接受，session_meta 不写 ``initial_persona_id`` 字段，
                效果与 002 完全一致。**新调用方推荐显式传**。
            session_id: 可选指定 session id（uuid 字符串）。``None``（默认）时
                由 :meth:`Session.new` 自动生成 uuid4。
                006 起新增以支持 ``agent-bridge`` 的 AG-UI 协议出口需要
                ``thread_id`` 与 ``session_id`` 一一对应的场景；该出口收到一个
                未知 ``thread_id`` 时会用它作为 ``session_id`` 创建新会话。
                **本参数仅当调用方有跨进程 / 跨协议的稳定 id 来源时使用**——
                CLI / 普通脚本继续不传，行为与 002 完全一致。
            channel: 初始 channel（007 起新增）。``"text"`` / ``"voice"``。
                ``"text"`` 时与 002~006 行为完全字节兼容（不写入 ``initial_channel``）。

        Returns:
            已落盘的新 Session 实例。

        Raises:
            SessionPersistError: 落盘失败（包括 ``session_id`` 已存在）。
            ValueError: ``channel`` 不在 ``("voice", "text")`` 中。
        """
        if channel not in ("voice", "text"):
            raise ValueError(f"channel 必须是 voice / text，实际: {channel!r}")
        session = Session.new(
            title=title or _default_title(),
            persona=persona,
            model=model,
            persona_id=persona_id,
            session_id=session_id,
            channel=channel,  # type: ignore[arg-type]
        )
        if title is None and self._title_generator is not None:
            generated = self._title_generator(session)
            if isinstance(generated, str) and generated.strip():
                session.initial_title = generated
                head = session.events[0]
                new_payload: dict[str, Any] = {**head.payload, "initial_title": generated}
                session.events[0] = type(head)(
                    type=head.type,
                    uuid=head.uuid,
                    ts=head.ts,
                    payload=new_payload,
                    meta=head.meta,
                )
        self._store.create(session)
        return session

    def open(self, session_id: str) -> Session:
        """完整加载指定会话。

        Raises:
            SessionNotFoundError: 不存在。
            SessionCorruptError: 文件损坏。
        """
        return self._store.load(session_id)

    def list(self) -> list[SessionSummary]:
        """列出所有会话摘要（按 ``updated_at`` 倒序）。"""
        return self._store.list()

    def delete(self, session_id: str) -> None:
        """删除指定会话（hard delete）。"""
        self._store.delete(session_id)

    def latest(self) -> Session | None:
        """最近活跃会话（完整加载）；无会话则 ``None``。

        Note:
            会读全文件——CLI 的 ``--resume`` 默认值用，调用频率低，可以接受。
            列表展示场景请用 :meth:`list`（O(1) per 文件）。
        """
        summary = self._store.latest()
        if summary is None:
            return None
        return self._store.load(summary.session_id)

    # ----- 装配 Conversation -----

    def start_conversation(
        self,
        session: Session,
        *,
        tool_hook_invoker: ToolHookInvoker | None = None,
        post_turn_external: bool = False,
    ) -> Conversation:
        """根据 ``session.current_persona_id / current_model`` 装配 :class:`Conversation`。

        Args:
            session: 目标 session（用 :meth:`create` 或 :meth:`open` 拿到）。
            tool_hook_invoker: 014 起新增；非 ``None`` 时 :meth:`Conversation._invoke_tool_safely`
                会把 tool 调用路由到 invoker（让 PreToolUse / PostToolUse hook 介入）。
                默认 ``None`` 保留 002~013 行为。
            post_turn_external: 014 起新增；``True`` 时 :meth:`Conversation.stream`
                finally 块不再调硬编码的 ``_observe_turn``——由外部（``AgentRuntime``
                的默认 PostTurn hook）接管 ``memory.observe``。默认 ``False`` 保留
                002~013 行为。

        Returns:
            完全装配好的 Conversation 实例，可直接调用 ``send`` / ``stream`` /
            ``switch_persona`` / ``switch_model``。

        Raises:
            AgentError: ``llm_client_factory`` / ``prompt_builder_factory`` /
                ``context_manager_factory`` 任一未注入。
            LLMAuthError / ValueError: ``llm_client_factory`` 抛出。
            PersonaNotFoundError: 当前 persona id（或老 session name fallback）
                在 catalog 找不到。
        """
        if (
            self._llm_client_factory is None
            or self._prompt_builder_factory is None
            or self._context_manager_factory is None
        ):
            raise AgentError(
                "start_conversation 需要 llm_client_factory / prompt_builder_factory / "
                "context_manager_factory 全部注入；当前 SessionManager 未配置完整。"
            )
        from ..conversation import Conversation  # 避免顶层循环 import
        from ..personas import PersonaCatalog  # 同上

        catalog = PersonaCatalog()

        # 解析当前 persona id：优先 session.current_persona_id；老 session（None）
        # fallback：按 current_persona_name 查 user 优先
        persona_id = session.current_persona_id
        if persona_id is None:
            from ..errors import PersonaNotFoundError

            try:
                info = catalog.find_by_name(session.current_persona_name)
                persona_id = info.id
            except PersonaNotFoundError:
                # 老 session 引用的 persona 已不存在 → fallback 到 builtin default
                import warnings

                from ..personas import BUILTIN_DEFAULT_PERSONA_ID

                warnings.warn(
                    f"老 session 引用的 persona name "
                    f"{session.current_persona_name!r} 在 catalog 找不到；"
                    f"fallback 到 builtin default",
                    stacklevel=2,
                )
                persona_id = BUILTIN_DEFAULT_PERSONA_ID

        llm_client = self._llm_client_factory(session.current_model)

        # 007 起：把 factory 包一层，让返回的 PromptBuilder 实例绑当前 session，
        # 使 ChannelSection 槽位能读 session.current_channel。如果 builder 没有
        # ``with_session`` 方法（自定义实现），保持原行为——向后兼容。
        base_factory = self._prompt_builder_factory

        def _session_aware_factory(pid: str) -> PromptBuilder:
            builder = base_factory(pid)
            with_session = getattr(builder, "with_session", None)
            if callable(with_session):
                bound: PromptBuilder = with_session(session)
                return bound
            return builder

        prompt_builder = _session_aware_factory(persona_id)
        # 009：每个会话独立产一个 context manager 实例（摘要策略持会话级状态）。
        context_manager = self._context_manager_factory()
        return Conversation(
            session=session,
            store=self._store,
            llm_client=llm_client,
            context_manager=context_manager,
            prompt_builder=prompt_builder,
            llm_client_factory=self._llm_client_factory,
            prompt_builder_factory=_session_aware_factory,
            catalog=catalog,
            memory=self._memory,
            tool_registry=self._tool_registry,
            tool_hook_invoker=tool_hook_invoker,
            post_turn_external=post_turn_external,
        )
