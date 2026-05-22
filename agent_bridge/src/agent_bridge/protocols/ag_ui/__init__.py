"""AG-UI 协议出口（持久化 / `thread_id` 无感自动创建）。

详见 docs/requirements/006-agent-bridge/design.md §4.4。
"""

from .routes import register_routes

__all__ = ["register_routes"]
