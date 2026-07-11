"""``SystemPromptComposer`` —— 有序的若干 :class:`Section` 的不可变装配。

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.1, §4.3。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, Protocol, runtime_checkable

from ..personas import PersonaCatalog
from ..sessions import Session
from .sections import PromptChannel, SystemPromptRenderContext

_PROJECT_IDENTITY_KEY = "project_identity"
_PERSONA_KEY = "persona"
_PERSONA_SWITCH_STRATEGY_KEY = "persona_switch_strategy"
_TOOL_USAGE_CONTRACT_KEY = "tool_usage_contract"
_MEMORY_CONSISTENCY_KEY = "memory_consistency"
_OUTPUT_CONTRACT_KEY = "output_contract"
_CHANNEL_KEY = "channel"
_RUNTIME_CONTEXT_KEY = "runtime_context"


@runtime_checkable
class Section(Protocol):
    """system_prompt 单个槽位的协议。

    实现类必须暴露：

    - ``key``：稳定的槽位标识，**只读**；用于装配（替换 / 关闭）和测试断言
    - :meth:`render`：输出该段文本；返回 ``None`` 表示本轮跳过该段（为未来
      按上下文条件输出预留扩展位）

    本期默认实现（:class:`StaticSection` / :class:`PersonaSection`）都是
    ``frozen=True`` 数据类，``key`` 字段对外是 read-only；Protocol 用
    ``@property`` 匹配此约束。
    """

    @property
    def key(self) -> str:
        """槽位标识，**只读**。"""
        ...

    def render(self, context: SystemPromptRenderContext | None = None) -> str | None:
        """渲染本段；返回 ``None`` 表示跳过。"""
        ...


@dataclass(frozen=True)
class SystemPromptComposer:
    """有序的若干 :class:`Section` 的不可变装配。

    Args:
        sections: 槽位元组。**插入顺序即渲染顺序**。每个 ``section.key`` 在
            元组内必须唯一（装配 API 依赖此前提）。

    渲染规则（见 :meth:`compose`）：

    - 按 ``sections`` 元组顺序遍历
    - 每个 section 调 ``render()``；返回 ``None`` 时跳过
    - 剩余文本以双换行（``"\\n\\n"``）拼接

    装配方法（:meth:`with_section` / :meth:`without` / :meth:`default`）都
    返回**新的 composer 实例**，原实例不变（``frozen=True`` + 元组存储）。
    """

    sections: tuple[Section, ...]

    DEFAULT_KEYS: ClassVar[tuple[str, ...]] = (
        _PROJECT_IDENTITY_KEY,
        _PERSONA_KEY,
        _PERSONA_SWITCH_STRATEGY_KEY,
        _TOOL_USAGE_CONTRACT_KEY,
        _MEMORY_CONSISTENCY_KEY,
        _OUTPUT_CONTRACT_KEY,
        _CHANNEL_KEY,
        _RUNTIME_CONTEXT_KEY,
    )
    """默认装配（:meth:`default`）下槽位的 key，按渲染顺序。

    005 起追加 ``runtime_context`` 槽位（最末尾）——按 cc 的"前部稳定指令、
    后部运行时变化信息"+ recency bias 原则放置。详见 005 design §4.10。

    007 起追加 ``channel`` 槽位——persona 给基调 → 切换策略 → 通道适应
    （"在这个通道下怎么说"）→ 运行时上下文。文字通道下
    ChannelSection.render() 返回 ``None``。详见 007 design §4.9.5。

    039 起在 ``persona_switch_strategy`` 与 ``channel`` 之间追加
    ``tool_usage_contract`` 与 ``memory_consistency`` 槽位。前者约束所有工具的
    调用陈述、证据来源和失败表达；后者约束被追问记忆矛盾时不暴露存储机制、
    不圆机制故事。

    之后追加 ``output_contract`` 槽位，承载跨通道的默认输出格式和可读性规则；
    ``channel`` 仍作为语音等具体通道的覆盖层。表达边界整体放在 persona /
    切换策略之后，通道 / 运行时上下文之前。
    """

    def compose(self, *, channel_override: PromptChannel | None = None) -> str:
        """渲染所有 section、按双换行拼接。

        Returns:
            最终 system_prompt 字符串。所有槽位都跳过时返回空串。
        """
        context = SystemPromptRenderContext(channel_override=channel_override)
        parts: list[str] = []
        for section in self.sections:
            rendered = section.render(context)
            if rendered is None:
                continue
            parts.append(rendered)
        return "\n\n".join(parts)

    def with_section(self, section: Section) -> SystemPromptComposer:
        """按 ``section.key`` 替换同 key 的槽位，返回新 composer。

        Args:
            section: 新的 Section 实现；其 ``key`` 必须与某现有槽位匹配。

        Returns:
            新的 composer 实例；原实例不变。

        Raises:
            KeyError: ``section.key`` 不在当前 composer 的槽位中。
        """
        target_key = section.key
        if not any(existing.key == target_key for existing in self.sections):
            raise KeyError(
                f"with_section: 找不到 key={target_key!r} 的槽位；"
                f"当前槽位: {[s.key for s in self.sections]}"
            )
        new_sections = tuple(
            section if existing.key == target_key else existing for existing in self.sections
        )
        return replace(self, sections=new_sections)

    def without(self, key: str) -> SystemPromptComposer:
        """移除指定 key 的槽位，返回新 composer。

        Args:
            key: 要移除的槽位 key。

        Returns:
            新的 composer 实例；原实例不变。

        Raises:
            KeyError: ``key`` 不在当前 composer 的槽位中。
        """
        new_sections = tuple(s for s in self.sections if s.key != key)
        if len(new_sections) == len(self.sections):
            raise KeyError(
                f"without: 找不到 key={key!r} 的槽位；当前槽位: {[s.key for s in self.sections]}"
            )
        return replace(self, sections=new_sections)

    @classmethod
    def default(
        cls,
        persona_id: str,
        *,
        catalog: PersonaCatalog,
        session: Session | None = None,
    ) -> SystemPromptComposer:
        """构造默认装配：8 个槽位按 :attr:`DEFAULT_KEYS` 顺序排列。

        - ``project_identity``：项目定位级硬约束（:class:`StaticSection`，
          文本来自 ``prompt_sections/project_identity.md``）
        - ``persona``：当前 persona body（:class:`PersonaSection`，动态读
          catalog）
        - ``persona_switch_strategy``：切换策略（:class:`StaticSection`，
          文本来自 ``prompt_sections/persona_switch_strategy.md``）
        - ``tool_usage_contract``：工具调用和工具结果陈述的通用边界
          （:class:`StaticSection`，文本来自
          ``prompt_sections/tool_usage_contract.md``）。039 起新增，避免未调用
          工具却声称查过，或向用户暴露工具名 / provider / schema / 底层机制。
        - ``memory_consistency``：被追问记忆矛盾时的回应边界
          （:class:`StaticSection`，文本来自
          ``prompt_sections/memory_consistency.md``）。039 起新增，禁止暴露
          存储机制 / 圆机制故事。
        - ``output_contract``：跨通道的默认输出表达约束
          （:class:`StaticSection`，文本来自
          ``prompt_sections/output_contract.md``）。约束 Markdown、链接、代码、
          清单等格式的默认使用方式，具体通道仍可在 ``channel`` 槽位覆盖。
        - ``channel``：当前通道指令（:class:`ChannelSection`，007 起新增；
          ``text`` 通道下不输出任何内容。渲染时可由本轮
          ``channel_override`` 覆盖 session 当前 channel；不传时回落到
          session 状态，与老调用方兼容）
        - ``runtime_context``：当前会话运行时上下文（:class:`RuntimeContextSection`，
          模板来自 ``prompt_sections/runtime_context.md``）。005 起新增，含
          当前时间、近期事实和历史归因的运行时证据边界。

        Args:
            persona_id: 目标 persona 的 UUID。
            catalog: persona 真相源；必传（避免内部凭空构造，便于测试注入）。
            session: 当前 :class:`Session`，:class:`ChannelSection` 持有它的引用
                作为无本轮 override 时的 fallback。002~006 调用方未传 ``session``
                时，channel 槽位会被跳过——保持完全向后兼容。**新调用方推荐显式传**。

        Returns:
            含 7~8 个默认槽位的 composer。``session is None`` 时返回 7 个
            槽位；传了 ``session`` 时多出 channel 槽位。
        """
        from .defaults import (
            load_default_channel_section,
            load_default_runtime_context_section,
            load_default_static_section,
        )
        from .sections import PersonaSection

        section_list: list[Section] = [
            load_default_static_section(_PROJECT_IDENTITY_KEY),
            PersonaSection(
                key=_PERSONA_KEY,
                persona_id=persona_id,
                catalog=catalog,
            ),
            load_default_static_section(_PERSONA_SWITCH_STRATEGY_KEY),
            load_default_static_section(_TOOL_USAGE_CONTRACT_KEY),
            load_default_static_section(_MEMORY_CONSISTENCY_KEY),
            load_default_static_section(_OUTPUT_CONTRACT_KEY),
        ]
        if session is not None:
            section_list.append(load_default_channel_section(session, _CHANNEL_KEY))
        section_list.append(load_default_runtime_context_section(_RUNTIME_CONTEXT_KEY))
        return cls(sections=tuple(section_list))
