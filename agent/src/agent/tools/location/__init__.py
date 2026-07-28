"""工具间共享的地点解析契约。"""

from .composite import CompositeLocationResolver
from .models import LocationCandidate
from .protocol import LocationResolver

__all__ = ["CompositeLocationResolver", "LocationCandidate", "LocationResolver"]
