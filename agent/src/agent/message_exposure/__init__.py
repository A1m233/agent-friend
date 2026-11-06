"""Versioned desktop display opportunities, separate from conversation history."""

from .store import MessageExposureStore, content_version

__all__ = ["MessageExposureStore", "content_version"]
