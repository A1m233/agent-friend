"""OpenAI ChatCompletion 协议出口。

详见 docs/requirements/006-agent-bridge/design.md §4.3。
"""

from .routes import register_routes

__all__ = ["register_routes"]
