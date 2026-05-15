"""``SessionManager`` —— 会话业务编排。

所有调用方（CLI / 未来 HTTP / 未来桌宠前端）通过本类访问会话能力：
**CRUD + 装配 Conversation**。Manager 不直接做文件 IO（委托 :class:`SessionStore`），
不直接调 LLM（委托 :class:`Conversation`）。

详见 docs/requirements/002-engine-session-management/design.md §4.4。

Note:
    M2.1 阶段 :meth:`start_conversation` 仅占位抛 ``NotImplementedError``；
    LLM / PromptBuilder / ContextManager 等装配依赖将在 M2.2 接入 Conversation 时实装。
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

    from ..context import ContextManager
    from ..conversation import Conversation
    from ..prompts import PromptBuilder


TitleGenerator = Callable[[Session], str]
LLMClientFactory = Callable[[str], "LLMClient"]
PromptBuilderFactory = Callable[[str], "PromptBuilder"]


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
        context_manager: 上下文管理策略。同上。
        title_generator: 可选；若提供，:meth:`create` 在未显式传 ``title`` 时
            会调用它，否则用 :func:`_default_title` 兜底。
    """

    def __init__(
        self,
        store: SessionStore,
        llm_client_factory: LLMClientFactory | None = None,
        prompt_builder_factory: PromptBuilderFactory | None = None,
        context_manager: ContextManager | None = None,
        title_generator: TitleGenerator | None = None,
    ) -> None:
        self._store = store
        self._llm_client_factory = llm_client_factory
        self._prompt_builder_factory = prompt_builder_factory
        self._context_manager = context_manager
        self._title_generator = title_generator

    # ----- 会话 CRUD -----

    def create(
        self,
        persona: str,
        model: str,
        title: str | None = None,
        *,
        persona_id: str | None = None,
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

        Returns:
            已落盘的新 Session 实例。

        Raises:
            SessionPersistError: 落盘失败。
        """
        session = Session.new(
            title=title or _default_title(),
            persona=persona,
            model=model,
            persona_id=persona_id,
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

    def start_conversation(self, session: Session) -> Conversation:
        """根据 ``session.current_persona_id / current_model`` 装配 :class:`Conversation`。

        Args:
            session: 目标 session（用 :meth:`create` 或 :meth:`open` 拿到）。

        Returns:
            完全装配好的 Conversation 实例，可直接调用 ``send`` / ``stream`` /
            ``switch_persona`` / ``switch_model``。

        Raises:
            AgentError: ``llm_client_factory`` / ``prompt_builder_factory`` /
                ``context_manager`` 任一未注入。
            LLMAuthError / ValueError: ``llm_client_factory`` 抛出。
            PersonaNotFoundError: 当前 persona id（或老 session name fallback）
                在 catalog 找不到。
        """
        if (
            self._llm_client_factory is None
            or self._prompt_builder_factory is None
            or self._context_manager is None
        ):
            raise AgentError(
                "start_conversation 需要 llm_client_factory / prompt_builder_factory / "
                "context_manager 全部注入；当前 SessionManager 未配置完整。"
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
        prompt_builder = self._prompt_builder_factory(persona_id)
        return Conversation(
            session=session,
            store=self._store,
            llm_client=llm_client,
            context_manager=self._context_manager,
            prompt_builder=prompt_builder,
            llm_client_factory=self._llm_client_factory,
            prompt_builder_factory=self._prompt_builder_factory,
            catalog=catalog,
        )
