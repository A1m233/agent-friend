"""YAML frontmatter helper for persona markdown files.

frontmatter 字段约定（详见 design §4.3）：

- ``id``: UUID v4 字符串，**必填**（持久化主键；缺失时由上层 lazy 自动补）
- ``description``: ``str | None``，可选简短描述

格式示例::

    ---
    id: 550e8400-e29b-41d4-a716-446655440000
    description: 默认朋友 Echo —— 中立平实的虚拟朋友
    ---
    你是一个名叫 Echo 的虚拟朋友 ...

容错策略：解析失败 / 未闭合 / 非 dict 都 **退化为"无 frontmatter"**——与 sessions
list 对损坏文件的容错对称，避免单文件错误让整个 catalog 挂掉。
"""

from __future__ import annotations

import warnings
from typing import Any, Final

import yaml

FRONTMATTER_DELIMITER: Final[str] = "---"


def parse(text: str) -> tuple[dict[str, Any], str]:
    """从 markdown 文本里剥离 YAML frontmatter。

    Args:
        text: 完整 markdown 文件内容。

    Returns:
        ``(frontmatter_dict, body)``：

        - 无 frontmatter（不以 ``---\\n`` 开头） → ``({}, text)``
        - 未闭合 / 非 dict / YAMLError → ``({}, text)`` + 必要时 :func:`warnings.warn`
        - 正常 → ``(dict, body)``，``body`` 不含 frontmatter 块

    Note:
        本函数 **不校验** ``id`` 字段是否存在；由上层（:class:`PersonaCatalog`）
        决定无 id 时的行为（lazy 自动补 + 写回）。
    """
    if not text.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return {}, text
    end = text.find(f"\n{FRONTMATTER_DELIMITER}\n", len(FRONTMATTER_DELIMITER) + 1)
    if end == -1:
        return {}, text
    fm_str = text[len(FRONTMATTER_DELIMITER) + 1 : end]
    body = text[end + len(FRONTMATTER_DELIMITER) + 2 :]
    try:
        fm = yaml.safe_load(fm_str)
    except yaml.YAMLError as e:
        warnings.warn(
            f"persona frontmatter 解析失败: {e}；退化为'无 frontmatter'",
            stacklevel=2,
        )
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, body


def serialize(content: str, *, id: str, description: str | None = None) -> str:
    """把 content + 元数据序列化为含 frontmatter 的完整 markdown 文本。

    Args:
        content: prompt 正文。
        id: UUID 字符串，**必填**。会写入 frontmatter 第一个字段。
        description: 可选简短描述；为 ``None`` 时不写入此字段。

    Returns:
        完整 markdown 文本，形如 ``---\\nid: ...\\ndescription: ...\\n---\\n<content>``。

    Note:
        ``sort_keys=False`` 保证 ``id`` 总是第一行——视觉一致，方便 review。
    """
    fm: dict[str, Any] = {"id": id}
    if description is not None:
        fm["description"] = description
    fm_str = yaml.safe_dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    return f"{FRONTMATTER_DELIMITER}\n{fm_str}\n{FRONTMATTER_DELIMITER}\n{content}"
