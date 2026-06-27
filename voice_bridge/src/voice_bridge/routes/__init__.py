"""voice_bridge HTTP 路由层。"""

from .control import register_control_routes
from .llm_proxy import register_llm_proxy_routes
from .transcription import register_transcription_routes

__all__ = ["register_control_routes", "register_llm_proxy_routes", "register_transcription_routes"]
