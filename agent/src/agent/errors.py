"""``agent`` 模块自有异常。

跟 :mod:`llm_providers.errors` 设计一致：上层调用方（CLI / 未来 API）
``catch`` 这些项目级异常做对外提示，**不直接暴露 Python 内置异常**
（如 ``FileNotFoundError``）。
"""

from __future__ import annotations


class AgentError(Exception):
    """所有 ``agent`` 模块异常的基类。"""


class PersonaNotFoundError(AgentError):
    """指定 ``persona_name`` 在 external_dir 与内置 personas 都找不到。"""
