"""默认 section 的资源加载工厂。

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.4。

资源文件随 ``agent`` 包发布，位于 ``agent.prompt_sections`` 子包；用
:func:`importlib.resources.files` 加载，与 003 ``_iter_builtin_files`` 同模式。
"""

from __future__ import annotations

from importlib.resources import files

from ..sessions import Session
from .sections import ChannelSection, RuntimeContextSection, StaticSection

_RESOURCE_PACKAGE = "agent.prompt_sections"

_SECTION_KEY_TO_FILENAME: dict[str, str] = {
    "project_identity": "project_identity.md",
    "persona_switch_strategy": "persona_switch_strategy.md",
    "runtime_context": "runtime_context.md",
    "channel": "channel_voice.md",
}


def _load_section_text(key: str) -> str:
    filename = _SECTION_KEY_TO_FILENAME[key]
    resource = files(_RESOURCE_PACKAGE) / filename
    return resource.read_text(encoding="utf-8").strip()


def load_default_static_section(key: str) -> StaticSection:
    """从包资源加载默认 section markdown 并构造 :class:`StaticSection`。

    Args:
        key: 默认槽位 key，必须是 :data:`_SECTION_KEY_TO_FILENAME` 中已知的
            key（``project_identity`` / ``persona_switch_strategy``）。

    Returns:
        StaticSection 实例，``text`` 为去除首尾空白的文件内容。

    Raises:
        KeyError: ``key`` 不是已知默认 key。
        FileNotFoundError: 资源文件缺失（包资产损坏，应在打包阶段发现）。
    """
    text = _load_section_text(key)
    return StaticSection(key=key, text=text)


def load_default_runtime_context_section(
    key: str = "runtime_context",
) -> RuntimeContextSection:
    """从包资源加载 ``runtime_context.md`` 模板并构造 :class:`RuntimeContextSection`。

    模板必须含 ``{current_time}`` 占位符；其它替换语义见 :class:`RuntimeContextSection`。

    Args:
        key: 槽位标识，默认 ``"runtime_context"``。

    Returns:
        RuntimeContextSection 实例，使用默认时钟（本地当前时间）。

    Raises:
        KeyError: ``key`` 不是 :data:`_SECTION_KEY_TO_FILENAME` 中已知的 key。
        FileNotFoundError: 资源文件缺失。
    """
    template = _load_section_text(key)
    return RuntimeContextSection(key=key, template=template)


def load_default_channel_section(session: Session, key: str = "channel") -> ChannelSection:
    """从包资源加载 ``channel_voice.md`` 模板并构造 :class:`ChannelSection`。

    007 起新增。

    Args:
        session: 目标 :class:`Session`；ChannelSection 持有它的引用，每次 render
            时按 :attr:`Session.current_channel` 决定输出。
        key: 槽位标识，默认 ``"channel"``。

    Returns:
        ChannelSection 实例。``text`` 通道下渲染 ``None``（不输出任何内容），
        ``voice`` 通道下输出 ``channel_voice.md`` 文件内容。

    Raises:
        KeyError: ``key`` 不是 :data:`_SECTION_KEY_TO_FILENAME` 中已知的 key。
        FileNotFoundError: 资源文件缺失。
    """
    voice_template = _load_section_text(key)
    return ChannelSection(key=key, session=session, voice_template=voice_template)
