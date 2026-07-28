"""Trusted interaction-surface identifiers and model-facing turn context."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NewType
from xml.sax.saxutils import quoteattr

InteractionSurfaceId = NewType("InteractionSurfaceId", str)

DESKTOP_CHAT_SURFACE = InteractionSurfaceId("desktop_chat")
VOICE_CALL_SURFACE = InteractionSurfaceId("voice_call")
IM_QQ_SURFACE = InteractionSurfaceId("im:qq")


@dataclass(frozen=True)
class InteractionSurfaceContext:
    """Structured attributes exposed to the model for one trusted surface."""

    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InteractionContextRenderer:
    """Render a trusted surface as a non-persistent current-turn context packet."""

    surfaces: Mapping[InteractionSurfaceId, InteractionSurfaceContext]

    def render(self, surface: InteractionSurfaceId) -> str:
        try:
            context = self.surfaces[surface]
        except KeyError as exc:
            raise ValueError(f"unsupported interaction surface: {surface}") from exc
        attributes = " ".join(f"{name}={quoteattr(value)}" for name, value in context.attributes)
        return f"<interaction_context>\n  <surface {attributes} />\n</interaction_context>"


DEFAULT_INTERACTION_CONTEXT_RENDERER = InteractionContextRenderer(
    surfaces={
        DESKTOP_CHAT_SURFACE: InteractionSurfaceContext(attributes=(("type", "desktop_chat"),)),
        VOICE_CALL_SURFACE: InteractionSurfaceContext(attributes=(("type", "voice_call"),)),
        IM_QQ_SURFACE: InteractionSurfaceContext(attributes=(("type", "im"), ("provider", "qq"))),
    }
)

__all__ = [
    "DEFAULT_INTERACTION_CONTEXT_RENDERER",
    "DESKTOP_CHAT_SURFACE",
    "IM_QQ_SURFACE",
    "VOICE_CALL_SURFACE",
    "InteractionContextRenderer",
    "InteractionSurfaceContext",
    "InteractionSurfaceId",
]
