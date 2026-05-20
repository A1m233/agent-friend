"""``Section`` 默认实现：

- :class:`StaticSection` —— 持有固定文本（004 起）
- :class:`PersonaSection` —— 动态读 persona body（004 起）
- :class:`RuntimeContextSection` —— 动态注入运行时上下文（005 起，详见
  005 design §4.10）

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.2。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..personas import PersonaCatalog


def _default_now() -> datetime:
    """默认时钟：取当前本地时间（带 tzinfo）。"""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class StaticSection:
    """持有固定文本的 section。

    Args:
        key: 槽位标识（与 :class:`SystemPromptComposer` 装配 API 配合）。
        text: 渲染时返回的文本。空字符串 / 仅空白时 :meth:`render` 返回 ``None``
            （视为该轮无内容）。
    """

    key: str
    text: str

    def render(self) -> str | None:
        """返回去除首尾空白后的文本；为空时返回 ``None``。"""
        stripped = self.text.strip()
        return stripped if stripped else None


@dataclass(frozen=True)
class PersonaSection:
    """动态从 :class:`PersonaCatalog` 读 persona body 的 section。

    Args:
        key: 槽位标识，约定为 ``"persona"``。
        persona_id: 目标 persona 的 UUID。
        catalog: persona 真相源；render 时调 ``catalog.read_content(persona_id)``。

    Raises:
        PersonaNotFoundError: :meth:`render` 时 ``persona_id`` 在 catalog
            找不到（与 003 ``MarkdownPromptBuilder.build`` 行为一致）。
    """

    key: str
    persona_id: str
    catalog: PersonaCatalog

    def render(self) -> str | None:
        """读 persona body；空内容时返回 ``None``。"""
        text = self.catalog.read_content(self.persona_id)
        stripped = text.strip()
        return stripped if stripped else None


@dataclass(frozen=True)
class RuntimeContextSection:
    """渲染时把"运行时上下文"注入 system_prompt。

    005 起新增。把当前时间 / knowledge cutoff 提示 / 工具使用强约束等
    "每次会话才确定"的信息合并成一段，让 LLM 知道：

    - 现在的真实时间（避免基于训练数据时期的"今天"作答）
    - 它不掌握 cutoff 后的信息（避免幻觉）
    - 涉及近期 / 实时信息**必须**用 :class:`agent.tools.builtin.web_search.WebSearchTool` 查
    - 整合搜索结果时保持人设语气（不暴露 AI 身份 / 不直接贴 URL）

    Args:
        key: 槽位标识，约定为 ``"runtime_context"``。
        template: 文本模板；必须包含 ``{current_time}`` 占位符；其它字符
            原样输出。**不**含 persona body / 切换策略——这些由各自的
            section 独立渲染。
        clock: 当前时间获取函数（``() -> datetime``）；默认取本地当前时间，
            **测试时可注入**固定时钟以保证 :meth:`render` 输出可断言。

    Note:
        本 section **每次 render 都重新调时钟**——同一 :class:`Conversation`
        内多轮对话间，越靠近发送时刻的时间被注入，跨 0 点用户问"今天"
        含义会自然滚动到新一天，不需要外部 invalidate。
    """

    key: str
    template: str
    clock: Callable[[], datetime] = field(default=_default_now)

    def render(self) -> str | None:
        """填充 ``{current_time}`` 占位符后返回结果。"""
        now = self.clock()
        current_time = now.strftime("%Y-%m-%d %H:%M")
        rendered = self.template.replace("{current_time}", current_time)
        stripped = rendered.strip()
        return stripped if stripped else None
