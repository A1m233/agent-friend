"""Persona 身份常量。

builtin persona 的 UUID **写死**在三处，必须保持一致：

1. 本文件（Python 常量）
2. ``agent/personas/{name}.md`` 的 frontmatter ``id`` 字段
3. 任何引用它的代码（如 :class:`MarkdownPromptBuilder` 的默认参数）

UUID 选择策略：对 builtin 用"标识性" UUID（前缀全 0），方便 review / 看
jsonl 历史时一眼识别"这是 builtin#N"，与 user 自建 persona（真随机 v4）显式区分。
未来 builtin 多了递增最后一位。

详见 docs/requirements/003-engine-persona-management/design.md §3.3。
"""

from __future__ import annotations

from typing import Final

BUILTIN_DEFAULT_PERSONA_ID: Final[str] = "00000000-0000-4000-8000-000000000001"
"""内置 ``default`` persona 的稳定 UUID。永远不变。

满足 UUID v4 格式要求（第 13 位 ``4`` = version，第 17 位 ``8`` = variant），
工具链不会拒收。
"""
