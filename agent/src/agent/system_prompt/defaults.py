"""默认 section 的资源加载工厂。

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.4。

资源文件随 ``agent`` 包发布，位于 ``agent.prompt_sections`` 子包；用
:func:`importlib.resources.files` 加载，与 003 ``_iter_builtin_files`` 同模式。
"""

from __future__ import annotations

from importlib.resources import files

from ..sessions import Session
from .sections import ChannelSection, StaticSection

_RESOURCE_PACKAGE = "agent.prompt_sections"

_SECTION_KEY_TO_FILENAME: dict[str, str] = {
    "project_identity": "project_identity.md",
    "evidence_policy": "evidence_policy.md",
    "persona_switch_strategy": "persona_switch_strategy.md",
    "relationship_contract": "relationship_contract.md",
    "untrusted_content": "untrusted_content.md",
    "tool_usage_contract": "tool_usage_contract.md",
    "memory_consistency": "memory_consistency.md",
    "output_contract": "output_contract.md",
    "interaction_context_contract": "interaction_context_contract.md",
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
            key（``project_identity`` / ``evidence_policy`` /
            ``persona_switch_strategy`` /
            ``relationship_contract`` / ``untrusted_content`` /
            ``tool_usage_contract`` / ``memory_consistency`` /
            ``output_contract``）。

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
) -> StaticSection:
    """从包资源加载稳定运行时政策，不在基础 prompt 中渲染具体时钟。

    具体时钟由 :class:`agent.tool_runtime.ToolRunContext` 每 turn 生成一次，并以
    ephemeral turn context 注入；同一份快照也作为隐藏参数传给时间相关工具。

    Args:
        key: 槽位标识，默认 ``"runtime_context"``。

    Returns:
        StaticSection 实例。

    Raises:
        KeyError: ``key`` 不是 :data:`_SECTION_KEY_TO_FILENAME` 中已知的 key。
        FileNotFoundError: 资源文件缺失。
    """
    return StaticSection(key=key, text=_load_section_text(key))


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
