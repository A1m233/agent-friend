"""``agent.sessions`` 子包专用异常。

跟 :class:`agent.errors.AgentError` 的设计一致：上层调用方（CLI / 未来 API）
``catch`` 这些项目级异常做对外提示，**不直接暴露 Python 内置异常**
（如 ``FileNotFoundError`` / ``OSError`` / ``json.JSONDecodeError``）。

详见 docs/requirements/002-engine-session-management/design.md §4.5。
"""

from __future__ import annotations

from ..errors import AgentError


class SessionError(AgentError):
    """所有 ``agent.sessions`` 模块异常的基类。"""


class SessionNotFoundError(SessionError):
    """目标 ``session_id`` 对应的会话不存在（文件被删 / id 错）。"""


class SessionPersistError(SessionError):
    """持久化 IO 失败（磁盘满、权限不足、网络盘断连等）。

    本期策略：底层 :class:`OSError` 会被包成本类抛出，由 CLI 红字提示但不中断主循环
    （详见 design §4.9）。
    """


class SessionCorruptError(SessionError):
    """会话文件结构损坏。

    典型场景：
    - 首行不是 ``session_meta`` 事件
    - 某行不是合法 JSON
    - 某行缺必有字段（``type`` / ``uuid`` / ``ts`` / ``payload``）
    """
