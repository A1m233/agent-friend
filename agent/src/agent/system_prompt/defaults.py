"""默认 section 的资源加载工厂。

详见 docs/requirements/004-engine-system-prompt-composer/design.md §4.4。

资源文件随 ``agent`` 包发布，位于 ``agent.prompt_sections`` 子包；用
:func:`importlib.resources.files` 加载，与 003 ``_iter_builtin_files`` 同模式。
"""

from __future__ import annotations

from importlib.resources import files

from .sections import StaticSection

_RESOURCE_PACKAGE = "agent.prompt_sections"

_SECTION_KEY_TO_FILENAME: dict[str, str] = {
    "project_identity": "project_identity.md",
    "persona_switch_strategy": "persona_switch_strategy.md",
}


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
    filename = _SECTION_KEY_TO_FILENAME[key]
    resource = files(_RESOURCE_PACKAGE) / filename
    text = resource.read_text(encoding="utf-8").strip()
    return StaticSection(key=key, text=text)
