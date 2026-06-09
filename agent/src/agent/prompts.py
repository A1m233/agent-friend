"""Prompt 构建器：决定 :class:`Conversation` 拿到什么 ``system_prompt``。

历史脉络：

- 001 (M0.2): 引入 :class:`MarkdownPromptBuilder`，按 name 加载 ``agent/personas/{name}.md``
- 001 (M0.3): 双层 overlay，user > builtin 同名覆盖
- 003: **重设计** —— name 退化为 user 维度唯一的 slug，**id 是主键**。
  :class:`MarkdownPromptBuilder` 改为持有 ``persona_id``，构建 prompt 时
  委托 :class:`PersonaCatalog.read_content`，**单一真相源**。
- 004: ``build()`` 委托 :class:`SystemPromptComposer`，按职责解耦的若干
  Section 组合输出（项目级硬约束 + persona body + 切换策略）。
  ``__init__`` 新增 keyword-only ``composer`` 参数；``None`` 时用
  :meth:`SystemPromptComposer.default` 构造默认装配。

frontmatter 解析 / id 寻址 / lazy 补 id 等都在 catalog 一处实现；section
装配 / 顺序 / 默认 markdown 加载都在 :mod:`agent.system_prompt` 一处实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .errors import PersonaNotFoundError
from .personas import PersonaCatalog
from .sessions import Session
from .system_prompt import SystemPromptComposer


class PromptBuilder(Protocol):
    """Prompt 构建器接口契约。"""

    def build(self) -> str:
        """返回组装好的 system prompt 字符串。"""
        ...


class MarkdownPromptBuilder:
    """004 实现：按 ``persona_id`` 委托 :class:`SystemPromptComposer` 输出
    按职责解耦的 system_prompt（项目级规则 + persona body + 切换策略）。

    Args:
        persona_id: 目标 persona 的 UUID。
        catalog: 可选 :class:`PersonaCatalog` 实例；不传则按 ``external_dir``
            构造一个新的（孵化期每次扫盘 OK）。
        external_dir: 用户自定义 persona 目录（仅当 ``catalog`` 未传时生效）。
            ``None`` 时由 :class:`~agent.PersonaCatalog` 用其默认目录
            （:func:`agent.paths.personas_dir`）。
        composer: 可选 :class:`SystemPromptComposer`；``None`` 时调
            :meth:`SystemPromptComposer.default` 构造含 4 个默认槽位的装配。
            调用方需要变体（如替换 ``persona_switch_strategy`` / 关闭某槽位）
            时自行构造并传入。
        session: 可选 :class:`Session`（007 起新增）。传入时
            :meth:`SystemPromptComposer.default` 会追加 :class:`ChannelSection`
            槽位，让 system_prompt 按 ``session.current_channel`` 动态调整。
            ``composer`` 显式传入时本参数被忽略（调用方需要在 composer 里自己
            装 ChannelSection）。

    Raises:
        PersonaNotFoundError: 调 :meth:`build` 时 ``persona_id`` 不存在。
    """

    def __init__(
        self,
        persona_id: str,
        *,
        catalog: PersonaCatalog | None = None,
        external_dir: Path | None = None,
        composer: SystemPromptComposer | None = None,
        session: Session | None = None,
    ):
        self.persona_id = persona_id
        self._catalog = catalog or PersonaCatalog(external_dir=external_dir)
        self._session = session
        self._composer = composer or SystemPromptComposer.default(
            persona_id,
            catalog=self._catalog,
            session=session,
        )

    def with_session(self, session: Session) -> MarkdownPromptBuilder:
        """返回同 persona / catalog 但绑定指定 session 的新 builder。

        007 起新增，用于 :meth:`agent.SessionManager.start_conversation` 把
        per-conversation 的 :class:`Session` 绑到工厂返回的 builder 上，让
        :class:`ChannelSection` 槽位生效。

        Note:
            返回的新 builder 重建了内部 composer——若调用方曾传入自定义
            ``composer``，本方法**不**保留它（语义优先级：session > 自定义 composer）。
        """
        return MarkdownPromptBuilder(
            persona_id=self.persona_id,
            catalog=self._catalog,
            session=session,
        )

    def build(self) -> str:
        """返回组合后的 system_prompt 字符串。

        Raises:
            PersonaNotFoundError: ``persona_id`` 在 catalog 找不到（由内部
                :class:`PersonaSection` 在渲染时抛出）。
        """
        return self._composer.compose()


__all__ = [
    "MarkdownPromptBuilder",
    "PersonaNotFoundError",
    "PromptBuilder",
]
