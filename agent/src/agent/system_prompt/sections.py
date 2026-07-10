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
from typing import Literal

from ..personas import PersonaCatalog
from ..sessions import Session

PromptChannel = Literal["voice", "text"]
"""单轮 prompt 渲染所采用的通道。"""


@dataclass(frozen=True)
class SystemPromptRenderContext:
    """本次 system prompt 渲染的运行时上下文。

    ``channel_override`` 表示这一次模型调用来自哪个交互入口。它优先于
    ``Session.current_channel``，避免 session 级生命周期状态污染本轮 prompt。
    """

    channel_override: PromptChannel | None = None


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

    def render(self, context: SystemPromptRenderContext | None = None) -> str | None:
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

    def render(self, context: SystemPromptRenderContext | None = None) -> str | None:
        """读 persona body；空内容时返回 ``None``。"""
        text = self.catalog.read_content(self.persona_id)
        stripped = text.strip()
        return stripped if stripped else None


@dataclass(frozen=True)
class RuntimeContextSection:
    """渲染时把"运行时上下文"注入 system_prompt。

    005 起新增。把当前时间 / knowledge cutoff 提示 / 运行时证据边界等
    "每次会话才确定"的信息合并成一段，让 LLM 知道：

    - 现在的真实时间（避免基于训练数据时期的"今天"作答）
    - 它不掌握 cutoff 后的信息（避免幻觉）
    - 涉及近期 / 实时信息必须先取得证据
    - 涉及历史归因时不能把当前上下文、外部事实或印象当成过去聊过的证据

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

    def render(self, context: SystemPromptRenderContext | None = None) -> str | None:
        """填充 ``{current_time}`` 占位符后返回结果。"""
        now = self.clock()
        current_time = now.strftime("%Y-%m-%d %H:%M")
        rendered = self.template.replace("{current_time}", current_time)
        stripped = rendered.strip()
        return stripped if stripped else None


@dataclass(frozen=True)
class ChannelSection:
    """根据本轮通道上下文输出对应的 system_prompt 片段。

    007 起新增。让 LLM 知道当前是语音 / 文字通道，生成对应表达风格的回复
    （语音通道下"短句、口语化、避免 markdown"等）。

    Args:
        key: 槽位标识，约定为 ``"channel"``。
        session: 目标 Session；未传本轮 override 时，render 读
            :attr:`Session.current_channel` 作为兼容 fallback。
        voice_template: ``"voice"`` 通道下输出的 prompt 文本（一般来自
            ``prompt_sections/channel_voice.md``）。
        text_template: ``"text"`` 通道下输出的 prompt 文本；默认空字符串——
            语义是"文字通道用 persona / 项目级指令默认风格即可，不再追加约束"。
            **空字符串 / 仅空白时 :meth:`render` 返回 ``None``**，与既有
            老 session（``current_channel="text"``）行为完全一致。

    Note:
        本 section 优先使用 ``SystemPromptRenderContext.channel_override``。
        这让同一个 session 能安全混合文字 / 语音等入口，避免 session 级
        生命周期状态污染本轮 prompt；没有 override 时才回落到
        :attr:`Session.current_channel`，保持旧调用方兼容。
    """

    key: str
    session: Session
    voice_template: str
    text_template: str = ""

    def render(self, context: SystemPromptRenderContext | None = None) -> str | None:
        """按本轮 override 或 ``session.current_channel`` 选模板并返回。"""
        channel = context.channel_override if context is not None else None
        if channel is None:
            channel = self.session.current_channel
        if channel == "voice":
            stripped = self.voice_template.strip()
            return stripped if stripped else None
        stripped = self.text_template.strip()
        return stripped if stripped else None
