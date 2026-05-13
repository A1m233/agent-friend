"""Prompt 构建器：决定 ``Conversation`` 拿到什么 ``system_prompt``。

阶段 1（M0.2）提供基础 :class:`MarkdownPromptBuilder`：从 ``agent/personas/{name}.md``
加载 markdown 文件作为 system prompt。

M0.3 扩展为**双层 overlay**：用户自定义人设（``data/personas/{name}.md``）
优先于内置默认（``agent/personas/{name}.md``）。

人设的语义内容（性格、行为准则等）**不在代码里硬编码**——只搭机制不填内容，
让用户在 markdown 里自由表达。

未来扩展（阶段 3 API 层）将引入 ``PersonaCatalog`` 类做 list/CRUD 管理，
**与本类共用同一个 ``external_dir`` 概念**——零返工。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.4。
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Protocol

from .errors import PersonaNotFoundError

DEFAULT_EXTERNAL_PERSONAS_DIR = Path("data/personas")
"""默认的用户自定义人设目录（项目根 ``data/personas/``，gitignored）。"""


class PromptBuilder(Protocol):
    """Prompt 构建器接口契约。"""

    def build(self) -> str:
        """返回组装好的 system prompt 字符串。"""
        ...


class MarkdownPromptBuilder:
    """阶段 1 实现：双层 overlay 加载 markdown 人设文件。

    Overlay 优先级（从高到低）：

    1. ``{external_dir}/{persona_name}.md`` — 用户自定义（runtime 可覆盖）
    2. ``agent/personas/{persona_name}.md`` — 内置默认（随包发布）

    Args:
        persona_name: 人设名称，对应 markdown 文件主名。默认 ``"default"``。
        external_dir: 用户自定义人设目录。``None`` 时用
            :data:`DEFAULT_EXTERNAL_PERSONAS_DIR`。**接受参数化**让未来 API 层
            能注入运行时配置（如多租户场景下每个用户独立目录），而不需要改
            本类签名。

    Raises:
        PersonaNotFoundError: 在 :meth:`build` 时，``external_dir`` 与内置
            personas 都找不到对应文件。
    """

    def __init__(
        self,
        persona_name: str = "default",
        external_dir: Path | None = None,
    ):
        self.persona_name = persona_name
        self._external_dir = external_dir or DEFAULT_EXTERNAL_PERSONAS_DIR

    def build(self) -> str:
        external_file = self._external_dir / f"{self.persona_name}.md"
        if external_file.is_file():
            return external_file.read_text(encoding="utf-8").strip()

        try:
            resource = files("agent.personas").joinpath(f"{self.persona_name}.md")
            return resource.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, ModuleNotFoundError) as e:
            raise PersonaNotFoundError(
                f"找不到人设 {self.persona_name!r}（已查 {external_file} 和 agent 内置 personas/）"
            ) from e
